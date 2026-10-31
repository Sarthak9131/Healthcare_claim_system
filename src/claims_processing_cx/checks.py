"""Deterministic claim checks and a mockable documentation judgment."""

from decimal import Decimal
from typing import Any

from claims_processing_cx.llm import LLMClient
from claims_processing_cx.models import Claim
from claims_processing_cx.privacy import PIIMasker
from claims_processing_cx.storage import Storage

_ICD10_CM = {"E11.9", "I10", "J06.9", "M54.5"}
_HCPCS_LEVEL_II = {"A0428", "G0008", "J1885"}
_ILLUSTRATIVE_PROCEDURES = {"ILL-PROC-001", "ILL-PROC-002"}
_ILLUSTRATIVE_DIAGNOSIS_RULES = {
    "ILL-PROC-001": {"J06.9", "I10"},
    "ILL-PROC-002": {"M54.5"},
}


class ClaimsChecks:
    """Rules for exact validations plus an isolated language-judgment seam."""

    def __init__(self, storage: Storage, llm: LLMClient, masker: PIIMasker) -> None:
        self.storage = storage
        self.llm = llm
        self.masker = masker

    async def eligibility(self, claim: Claim) -> dict[str, Any]:
        """Check member and policy coverage on each service date."""
        member = self.storage.get_member(claim.member_id or "")
        if member is None:
            return self._result("eligibility", False, "CO-16", "Member record was not found.")
        policy = self.storage.get_policy(member.policy_id)
        service_dates = [line.service_date for line in claim.service_lines]
        if policy is None or any(service_date is None for service_date in service_dates):
            return self._result(
                "eligibility", False, "CO-16", "Coverage or service-date data is incomplete."
            )
        dates = [service_date for service_date in service_dates if service_date is not None]
        active = all(
            member.coverage_start <= service_date
            and (member.coverage_end is None or service_date <= member.coverage_end)
            and policy.effective_date <= service_date
            and (policy.termination_date is None or service_date <= policy.termination_date)
            for service_date in dates
        )
        provider = self.storage.get_provider(claim.provider_id or "")
        network = (
            "IN_NETWORK"
            if provider is not None and member.policy_id in provider.in_network_policy_ids
            else "OUT_OF_NETWORK"
        )
        return self._result(
            "eligibility",
            active,
            None if active else "CO-27",
            "Coverage is active for all service dates."
            if active
            else "Coverage is not active for one or more service dates.",
            metadata={"policy_id": member.policy_id, "network_status": network},
        )

    async def code_validation(self, claim: Claim) -> dict[str, Any]:
        """Validate ICD-10-CM, HCPCS Level II, and local illustrative code consistency."""
        codes = set(claim.diagnosis_codes)
        invalid_diagnoses = codes - _ICD10_CM
        invalid_hcpcs = {
            line.hcpcs_code
            for line in claim.service_lines
            if line.hcpcs_code not in _HCPCS_LEVEL_II
        }
        invalid_procedures = {
            line.procedure_code
            for line in claim.service_lines
            if line.procedure_code not in _ILLUSTRATIVE_PROCEDURES
        }
        mismatch = False
        for line in claim.service_lines:
            line_diagnoses = set(line.diagnosis_codes or claim.diagnosis_codes)
            allowed_diagnoses = _ILLUSTRATIVE_DIAGNOSIS_RULES.get(
                line.procedure_code or "", set()
            )
            if not line_diagnoses & allowed_diagnoses:
                mismatch = True
                break
        duplicate_lines = self._duplicate_service_lines(claim)
        invalid = bool(invalid_diagnoses or invalid_hcpcs or invalid_procedures or mismatch)
        reason = "CO-50" if mismatch else "CO-16" if invalid else None
        details = "Code subset and illustrative consistency rules passed."
        if mismatch:
            details = "Diagnosis did not match the local illustrative procedure rule."
        elif duplicate_lines:
            details = "Repeated service lines require a bundling review."
        elif invalid:
            details = "One or more codes are outside the configured public or illustrative subset."
        return self._result(
            "code_validation",
            not invalid and not duplicate_lines,
            "CO-97" if duplicate_lines else reason,
            details,
            metadata={
                "invalid_diagnoses": sorted(invalid_diagnoses),
                "invalid_hcpcs": sorted(code for code in invalid_hcpcs if code),
                "invalid_procedures": sorted(code for code in invalid_procedures if code),
                "duplicate_service_lines": duplicate_lines,
            },
        )

    async def duplicate_detection(self, claim: Claim) -> dict[str, Any]:
        """Detect explicit and previously received duplicate claims."""
        signature = self._claim_signature(claim)
        older_match = any(
            other.claim_id != claim.claim_id
            and self._claim_signature(other) == signature
            and other.received_date is not None
            and claim.received_date is not None
            and (other.received_date, other.claim_id) < (claim.received_date, claim.claim_id)
            for other in self.storage.list_claims()
        )
        duplicate = claim.duplicate_of_claim_id is not None or older_match
        return self._result(
            "duplicate_detection",
            not duplicate,
            "CO-18" if duplicate else None,
            "A prior claim with the same service signature was found."
            if duplicate
            else "No earlier claim with the same service signature was found.",
        )

    async def prior_authorization(self, claim: Claim) -> dict[str, Any]:
        """Check policy authorization requirements for billed HCPCS codes."""
        member = self.storage.get_member(claim.member_id or "")
        policy = self.storage.get_policy(member.policy_id) if member else None
        if policy is None:
            return self._result(
                "prior_authorization", False, "CO-16", "Policy could not be resolved."
            )
        required = {
            line.hcpcs_code
            for line in claim.service_lines
            if line.hcpcs_code in policy.benefits.prior_authorization_codes
        }
        missing = bool(required) and not claim.prior_authorization_id
        return self._result(
            "prior_authorization",
            not missing,
            "CO-197" if missing else None,
            "Prior authorization is present or not required."
            if not missing
            else "Required prior authorization was not supplied.",
            metadata={"required_codes": sorted(code for code in required if code)},
        )

    async def coordination_of_benefits(self, claim: Claim) -> dict[str, Any]:
        """Check secondary payer information without assuming payer order rules."""
        member = self.storage.get_member(claim.member_id or "")
        needs_cob = member is not None and member.secondary_payer_id is not None
        present = not needs_cob or claim.other_payer_paid > Decimal("0")
        return self._result(
            "coordination_of_benefits",
            present,
            "COB-INFO" if not present else None,
            "Secondary-payer information is present or not applicable."
            if present
            else "A secondary payer is recorded but other-payer payment is missing.",
        )

    async def fraud_waste_abuse(self, claim: Claim) -> dict[str, Any]:
        """Flag simple high-dollar and repeated-line anomalies for human review."""
        repeated_lines = self._duplicate_service_lines(claim)
        high_dollar = claim.billed_amount >= Decimal("10000.00")
        suspicious = bool(repeated_lines or high_dollar)
        return self._result(
            "fraud_waste_abuse",
            not suspicious,
            "FWA-REVIEW" if suspicious else None,
            "A deterministic anomaly threshold requires human review."
            if suspicious
            else "No configured synthetic anomaly threshold was triggered.",
            metadata={"high_dollar": high_dollar, "repeated_service_lines": repeated_lines},
        )

    async def documentation_consistency(self, claim: Claim) -> dict[str, Any]:
        """Use the LLM seam only for free-text consistency judgment."""
        documentation = claim.clinical_documentation
        if not documentation:
            return self._result(
                "documentation_consistency",
                True,
                None,
                "No narrative documentation was supplied; no language judgment was needed.",
                confidence=Decimal("1.0"),
            )
        masked_documentation = self.masker.mask_text(documentation)
        consistent, confidence, rationale = await self.llm.review_documentation(
            masked_documentation
        )
        return self._result(
            "documentation_consistency",
            consistent,
            None if consistent else "DOC-REVIEW",
            rationale,
            confidence=Decimal(str(confidence)),
        )

    @staticmethod
    def _result(
        name: str,
        passed: bool,
        reason_code: str | None,
        details: str,
        *,
        confidence: Decimal = Decimal("1.0"),
        metadata: dict[str, str | int | float | bool | None | list[str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "check_name": name,
            "passed": passed,
            "reason_code": reason_code,
            "details": details,
            "confidence": confidence,
            "metadata": metadata or {},
        }

    @staticmethod
    def _claim_signature(claim: Claim) -> tuple[str | None, str | None, tuple[str, ...]]:
        return (
            claim.member_id,
            claim.provider_id,
            tuple(
                sorted(
                    f"{line.service_date}:{line.procedure_code}:{line.hcpcs_code}:{line.units}"
                    for line in claim.service_lines
                )
            ),
        )

    @staticmethod
    def _duplicate_service_lines(claim: Claim) -> bool:
        signatures = [
            (line.service_date, line.procedure_code, line.hcpcs_code)
            for line in claim.service_lines
        ]
        return len(signatures) != len(set(signatures))
