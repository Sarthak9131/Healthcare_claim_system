"""Sequential intake, parallel checks, and reviewable claim decision chain."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import ValidationError

from claims_processing_cx.audit import AuditEvent, AuditTrail, stable_hash
from claims_processing_cx.checks import ClaimsChecks
from claims_processing_cx.intake import intake_error_text, normalize_claim
from claims_processing_cx.llm import LLMClient
from claims_processing_cx.models import (
    CheckResult,
    Claim,
    Decision,
    DecisionStatus,
    RankedReasonCode,
)
from claims_processing_cx.privacy import PIIMasker
from claims_processing_cx.storage import Storage

_REASON_DESCRIPTIONS = {
    "CO-16": "Required claim information is missing or invalid.",
    "CO-18": "The claim appears to be a duplicate.",
    "CO-27": "Coverage was not active for the service date.",
    "CO-50": "The service did not meet the configured illustrative coverage rule.",
    "CO-97": "Repeated service lines require a bundling review.",
    "CO-197": "Required prior authorization was not supplied.",
    "COB-INFO": "Other-payer information requires review.",
    "FWA-REVIEW": "A configured anomaly threshold requires human review.",
    "SCHEMA-001": "The claim could not be safely validated.",
    "DOC-REVIEW": "Clinical documentation needs human review.",
    "CHECK-FAILURE": "A backend check could not be validated.",
    "PR-1": "A deductible amount was applied.",
    "PR-2": "A coinsurance amount was applied.",
    "SYN-HIGH-DOLLAR": "The billed amount exceeds the prototype review threshold.",
}

_DENYING_CHECKS = {
    "eligibility",
    "code_validation",
    "duplicate_detection",
    "prior_authorization",
}


@dataclass(frozen=True)
class CheckExecution:
    """Validated check response and whether repeated validation failed."""

    result: CheckResult
    failed_validation: bool


class ClaimsPipeline:
    """Runs exact adjudication rules locally and persists an audit trail."""

    def __init__(
        self,
        storage: Storage,
        llm: LLMClient,
        audit_trail: AuditTrail,
        *,
        high_dollar_threshold: Decimal = Decimal("10000.00"),
    ) -> None:
        self.storage = storage
        self.audit_trail = audit_trail
        self.masker = PIIMasker(storage)
        self.audit_trail.masker = self.masker
        self.checks = ClaimsChecks(storage, llm, self.masker)
        self.high_dollar_threshold = high_dollar_threshold

    async def adjudicate(self, payload: dict[str, Any]) -> Decision:
        """Validate one raw claim and run the complete decision chain."""
        fallback_claim_id = self._safe_claim_id(payload)
        intake_started = time.perf_counter()
        try:
            claim = normalize_claim(payload)
        except (ValidationError, ValueError, TypeError) as error:
            detail = intake_error_text(error)
            decision = self._review_decision(
                fallback_claim_id,
                "SCHEMA-001",
                f"Claim intake requires human review. {detail}",
            )
            self._record(
                fallback_claim_id,
                "intake",
                time.perf_counter() - intake_started,
                payload,
                {"valid": False, "reason_code": "SCHEMA-001"},
                [detail],
            )
            self.storage.save_decision(decision)
            self._record(
                fallback_claim_id,
                "final_decision",
                0,
                {"claim_id": fallback_claim_id},
                decision.model_dump(mode="json"),
            )
            return decision

        self.storage.save_claim(claim)
        self._record(
            claim.claim_id,
            "intake",
            time.perf_counter() - intake_started,
            payload,
            {"valid": True, "service_line_count": len(claim.service_lines)},
        )

        check_names: tuple[str, ...] = (
            "eligibility",
            "code_validation",
            "duplicate_detection",
            "prior_authorization",
            "coordination_of_benefits",
            "fraud_waste_abuse",
            "documentation_consistency",
        )
        phase_started = time.perf_counter()
        executions = await asyncio.gather(
            *(self._run_check(claim, name) for name in check_names)
        )
        self._record(
            claim.claim_id,
            "parallel_checks",
            time.perf_counter() - phase_started,
            claim.model_dump(mode="json"),
            {"checks": [item.result.model_dump(mode="json") for item in executions]},
            [
                item.result.details
                for item in executions
                if item.failed_validation
            ],
        )

        adjudicator_started = time.perf_counter()
        adjudicator_decision = self._adjudicate(claim, executions)
        self._record(
            claim.claim_id,
            "adjudicator",
            time.perf_counter() - adjudicator_started,
            {"claim_id": claim.claim_id, "checks": check_names},
            adjudicator_decision.model_dump(mode="json"),
        )

        compliance_started = time.perf_counter()
        final_decision = self._compliance_review(claim, adjudicator_decision, executions)
        self._record(
            claim.claim_id,
            "compliance_review",
            time.perf_counter() - compliance_started,
            adjudicator_decision.model_dump(mode="json"),
            final_decision.model_dump(mode="json"),
        )
        self.storage.save_decision(final_decision)
        self._record(
            claim.claim_id,
            "final_decision",
            0,
            {"claim_id": claim.claim_id},
            final_decision.model_dump(mode="json"),
        )
        return final_decision

    async def _run_check(self, claim: Claim, name: str) -> CheckExecution:
        """Retry malformed check outputs twice and validate every attempt."""
        check: Callable[[Claim], Awaitable[object]] = getattr(self.checks, name)
        started = time.perf_counter()
        errors: list[str] = []
        for attempt in range(3):
            try:
                output = await check(claim)
                result = CheckResult.model_validate(output)
                if result.check_name != name:
                    raise ValueError(f"Expected check_name {name!r}, got {result.check_name!r}")
                elapsed = time.perf_counter() - started
                self._record(
                    claim.claim_id,
                    name,
                    elapsed,
                    claim.model_dump(mode="json"),
                    result.model_dump(mode="json"),
                    errors,
                )
                return CheckExecution(result=result, failed_validation=False)
            except Exception as error:
                errors.append(f"attempt {attempt + 1}: {type(error).__name__}")
        result = CheckResult(
            check_name=name,
            passed=False,
            reason_code="CHECK-FAILURE",
            details="Check output failed Pydantic validation after two retries.",
        )
        self._record(
            claim.claim_id,
            name,
            time.perf_counter() - started,
            claim.model_dump(mode="json"),
            result.model_dump(mode="json"),
            errors,
        )
        return CheckExecution(result=result, failed_validation=True)

    def _adjudicate(self, claim: Claim, checks: list[CheckExecution]) -> Decision:
        """Apply deterministic denial rules and toy-plan benefit arithmetic."""
        results = [execution.result for execution in checks]
        failed_results = [result for result in results if not result.passed]
        failed_names = {result.check_name for result in failed_results}
        requires_review = any(execution.failed_validation for execution in checks)
        review_codes = {"FWA-REVIEW", "COB-INFO", "DOC-REVIEW"}
        requires_review = requires_review or any(
            result.reason_code in review_codes
            or (
                result.check_name == "documentation_consistency"
                and result.confidence < Decimal("0.65")
            )
            for result in results
        )

        denial_reasons = [
            result
            for result in failed_results
            if result.check_name in _DENYING_CHECKS
        ]
        if requires_review:
            reason = next(
                (
                    result.reason_code
                    for result in failed_results
                    if result.reason_code in review_codes
                ),
                "CHECK-FAILURE" if any(item.failed_validation for item in checks) else "DOC-REVIEW",
            )
            return self._review_decision(
                claim.claim_id,
                reason or "DOC-REVIEW",
                "A configured human-review condition was triggered; "
                "no automated payment was issued.",
                reason_codes=self._ranked([result.reason_code for result in failed_results]),
            )
        if denial_reasons or failed_names & _DENYING_CHECKS:
            reason_codes = self._ranked([result.reason_code for result in denial_reasons])
            return Decision(
                claim_id=claim.claim_id,
                status=DecisionStatus.DENIED,
                allowed_amount=Decimal("0.00"),
                paid_amount=Decimal("0.00"),
                member_responsibility=Decimal("0.00"),
                reason_codes=reason_codes,
                explanation="The claim did not pass one or more exact coverage or code checks.",
            )

        member = self.storage.get_member(claim.member_id or "")
        policy = self.storage.get_policy(member.policy_id) if member else None
        if policy is None:
            return self._review_decision(
                claim.claim_id, "CHECK-FAILURE", "Policy data could not be resolved."
            )
        network = next(
            (
                str(result.metadata.get("network_status"))
                for result in results
                if result.check_name == "eligibility"
            ),
            "OUT_OF_NETWORK",
        )
        benefits = policy.benefits
        allowed = claim.billed_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        deductible_remaining = max(
            Decimal("0.00"), benefits.annual_deductible - benefits.deductible_met
        )
        deductible_applied = min(allowed, deductible_remaining)
        post_deductible = allowed - deductible_applied
        coinsurance = (
            benefits.out_of_network_coinsurance_rate
            if network == "OUT_OF_NETWORK"
            else benefits.coinsurance_rate
        )
        member_coinsurance = post_deductible * coinsurance
        plan_liability = post_deductible - member_coinsurance
        paid = max(Decimal("0.00"), plan_liability - claim.other_payer_paid)
        member_responsibility = deductible_applied + member_coinsurance
        paid = paid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        member_responsibility = member_responsibility.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        reasons: list[str] = []
        if deductible_applied > 0:
            reasons.append("PR-1")
        if member_coinsurance > 0:
            reasons.append("PR-2")
        status = (
            DecisionStatus.PAID
            if paid >= allowed and allowed > 0
            else DecisionStatus.PARTIALLY_PAID
            if paid > 0 or member_responsibility > 0
            else DecisionStatus.DENIED
        )
        explanation = (
            "The claim passed configured exact checks. Amounts use the synthetic benefit schedule."
        )
        if network == "OUT_OF_NETWORK":
            explanation += " Out-of-network cost sharing was applied."
        return Decision(
            claim_id=claim.claim_id,
            status=status,
            allowed_amount=allowed,
            paid_amount=paid,
            member_responsibility=member_responsibility,
            reason_codes=self._ranked(reasons),
            explanation=explanation,
        )

    def _compliance_review(
        self,
        claim: Claim,
        decision: Decision,
        checks: list[CheckExecution],
    ) -> Decision:
        """Validate decision invariants and pend high-dollar or low-confidence claims."""
        review_reason: str | None = None
        if claim.billed_amount >= self.high_dollar_threshold:
            review_reason = "SYN-HIGH-DOLLAR"
        elif any(item.failed_validation for item in checks):
            review_reason = "CHECK-FAILURE"
        elif any(
            item.result.check_name == "documentation_consistency"
            and item.result.confidence < Decimal("0.65")
            for item in checks
        ):
            review_reason = "DOC-REVIEW"
        if review_reason:
            decision = self._review_decision(
                claim.claim_id,
                review_reason,
                "Compliance review routed this claim to a human reviewer; "
                "no automated payment was issued.",
                reason_codes=self._ranked(
                    [reason.code for reason in decision.reason_codes] + [review_reason]
                ),
            )
        if decision.paid_amount > decision.allowed_amount:
            decision = self._review_decision(
                claim.claim_id,
                "CHECK-FAILURE",
                "A decision amount invariant failed validation.",
            )
        return Decision.model_validate(decision.model_dump())

    def _record(
        self,
        claim_id: str,
        step: str,
        latency_seconds: float,
        input_value: object,
        output: dict[str, Any],
        errors: list[str] | None = None,
    ) -> None:
        """Persist a structured step event using a digest instead of raw input."""
        masked_input = self.masker.mask_value(input_value)
        self.audit_trail.record(
            AuditEvent(
                claim_id=claim_id,
                step=step,
                latency_ms=max(0.0, latency_seconds * 1000),
                input_hash=stable_hash(masked_input),
                output=self.masker.mask_value(output),
                errors=self.masker.mask_value(errors or []),
                token_usage=0,
            )
        )

    @staticmethod
    def _ranked(codes: Sequence[str | None]) -> list[RankedReasonCode]:
        """Create stable, deduplicated reason codes with human-readable labels."""
        unique_codes = list(dict.fromkeys(code for code in codes if code))
        return [
            RankedReasonCode(
                code=code,
                description=_REASON_DESCRIPTIONS.get(code, "Synthetic review reason."),
                rank=rank,
            )
            for rank, code in enumerate(unique_codes, start=1)
        ]

    @classmethod
    def _review_decision(
        cls,
        claim_id: str,
        reason_code: str,
        explanation: str,
        *,
        reason_codes: list[RankedReasonCode] | None = None,
    ) -> Decision:
        """Build a no-payment review decision."""
        ranked = reason_codes or cls._ranked([reason_code])
        if not any(reason.code == reason_code for reason in ranked):
            ranked.append(
                RankedReasonCode(
                    code=reason_code,
                    description=_REASON_DESCRIPTIONS.get(reason_code, "Synthetic review reason."),
                    rank=len(ranked) + 1,
                )
            )
        return Decision(
            claim_id=claim_id,
            status=DecisionStatus.PENDED_FOR_REVIEW,
            allowed_amount=Decimal("0.00"),
            paid_amount=Decimal("0.00"),
            member_responsibility=Decimal("0.00"),
            reason_codes=ranked,
            explanation=explanation,
            requires_human_review=True,
        )

    @staticmethod
    def _safe_claim_id(payload: dict[str, Any]) -> str:
        """Avoid reflecting malformed or unsafe identifiers into logs and filenames."""
        claim_id = payload.get("claim_id")
        if isinstance(claim_id, str) and claim_id and all(
            character.isalnum() or character in "-_" for character in claim_id
        ):
            return claim_id[:80]
        return f"INVALID-{stable_hash(payload)[:12]}"
