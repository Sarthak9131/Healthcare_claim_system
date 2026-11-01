"""FastAPI endpoints and the local Dialogflow CX webhook contract."""

import os
import secrets
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException

from claims_processing_cx.audit import AuditTrail
from claims_processing_cx.llm import StubLLMClient
from claims_processing_cx.models import (
    Appeal,
    Decision,
    DecisionStatus,
    Member,
    RankedReasonCode,
    StrictModel,
)
from claims_processing_cx.pipeline import ClaimsPipeline
from claims_processing_cx.storage import InMemoryStorage, Storage
from claims_processing_cx.synthetic import create_synthetic_dataset


class AppealSubmission(StrictModel):
    """Request body for an appeal associated with a claim."""

    claim_id: str
    member_id: str
    appeal_text: str


class VerifiedClaimRequest(StrictModel):
    """Identity-gated CX tool request."""

    claim_id: str
    member_id: str
    date_of_birth: date


class VerifiedAppealRequest(VerifiedClaimRequest):
    """Identity-gated CX appeal tool request."""

    appeal_text: str


class ClaimStatusToolResponse(StrictModel):
    """Minimum necessary claim status and backend-provided explanation facts."""

    status: DecisionStatus
    reason_codes: list[RankedReasonCode]
    allowed_amount: str
    paid_amount: str
    member_responsibility: str


class AppealToolResponse(StrictModel):
    """Confirmation that appeal intake was accepted."""

    status: str


def create_app(
    storage: Storage | None = None,
    *,
    audit_directory: Path | None = None,
    webhook_token: str | None = None,
) -> FastAPI:
    """Create an isolated app instance for local execution or tests."""
    data_store = storage or InMemoryStorage(create_synthetic_dataset())
    audit_trail = AuditTrail(audit_directory or Path("data/audit"))
    pipeline = ClaimsPipeline(data_store, StubLLMClient(), audit_trail)
    configured_token = webhook_token or os.getenv("CX_WEBHOOK_TOKEN")
    app = FastAPI(title="Synthetic Claims Processing API", version="0.1.0")
    app.state.storage = data_store
    app.state.pipeline = pipeline

    @app.get("/")
    async def root() -> dict[str, str]:
        """Expose a useful local landing response instead of a root 404."""
        return {
            "service": "Synthetic Claims Processing API",
            "mode": "mock",
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Report local service readiness without external dependency checks."""
        return {"status": "ok", "mode": "mock"}

    @app.post("/adjudicate", response_model=Decision)
    async def adjudicate(payload: dict[str, Any]) -> Decision:
        """Adjudicate one synthetic 837-style claim payload."""
        return await pipeline.adjudicate(payload)

    @app.get("/claims/{claim_id}/status", response_model=Decision)
    async def claim_status(claim_id: str) -> Decision:
        """Return the latest adjudication decision for a claim."""
        decision = data_store.get_decision(claim_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="No decision exists for this claim.")
        return decision

    @app.post("/appeals", response_model=Appeal)
    async def submit_appeal(submission: AppealSubmission) -> Appeal:
        """Accept appeal intake only for the synthetic member attached to the claim."""
        claim = data_store.get_claim(submission.claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="Claim not found.")
        if claim.member_id != submission.member_id:
            raise HTTPException(status_code=403, detail="Member does not match the claim.")
        appeal = Appeal(
            appeal_id=f"SYN-APPEAL-{uuid4().hex[:12].upper()}",
            claim_id=submission.claim_id,
            member_id=submission.member_id,
            appeal_text=submission.appeal_text,
        )
        data_store.save_appeal(appeal)
        return appeal

    @app.get("/review-queue", response_model=list[Decision])
    async def review_queue() -> list[Decision]:
        """List claims held for human review."""
        return [
            decision
            for decision in data_store.list_decisions()
            if decision.status == DecisionStatus.PENDED_FOR_REVIEW
        ]

    @app.post("/cx/tools/claim-status", response_model=ClaimStatusToolResponse)
    async def cx_claim_status(
        request: VerifiedClaimRequest,
        authorization: str | None = Header(default=None),
    ) -> ClaimStatusToolResponse:
        """Return minimum claim facts only after matching member ID and DOB."""
        _verify_webhook_token(authorization, configured_token)
        member = _verified_member(data_store, request.member_id, request.date_of_birth.isoformat())
        claim = data_store.get_claim(request.claim_id)
        if member is None or claim is None or claim.member_id != member.member_id:
            raise HTTPException(status_code=404, detail="Verified claim was not found.")
        decision = data_store.get_decision(request.claim_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="Claim result is not available.")
        return ClaimStatusToolResponse(
            status=decision.status,
            reason_codes=decision.reason_codes,
            allowed_amount=str(decision.allowed_amount),
            paid_amount=str(decision.paid_amount),
            member_responsibility=str(decision.member_responsibility),
        )

    @app.post("/cx/tools/appeals", response_model=AppealToolResponse)
    async def cx_submit_appeal(
        request: VerifiedAppealRequest,
        authorization: str | None = Header(default=None),
    ) -> AppealToolResponse:
        """Accept identity-verified appeal text without deciding its merits."""
        _verify_webhook_token(authorization, configured_token)
        member = _verified_member(data_store, request.member_id, request.date_of_birth.isoformat())
        claim = data_store.get_claim(request.claim_id)
        if (
            member is None
            or claim is None
            or claim.member_id != member.member_id
            or not request.appeal_text.strip()
        ):
            raise HTTPException(status_code=404, detail="Verified appeal could not be accepted.")
        appeal = Appeal(
            appeal_id=f"SYN-APPEAL-{uuid4().hex[:12].upper()}",
            claim_id=request.claim_id,
            member_id=member.member_id,
            appeal_text=request.appeal_text,
        )
        data_store.save_appeal(appeal)
        return AppealToolResponse(status="RECEIVED")

    @app.post("/webhook")
    async def dialogflow_webhook(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Handle the standard Dialogflow CX webhook request in local mode."""
        _verify_webhook_token(authorization, configured_token)

        fulfillment_info = _as_dict(payload.get("fulfillmentInfo"))
        session_info = _as_dict(payload.get("sessionInfo"))
        parameters = _as_dict(session_info.get("parameters"))
        tag = fulfillment_info.get("tag")

        member = _verified_member(
            data_store,
            parameters.get("member_id"),
            parameters.get("date_of_birth"),
        )
        if member is None:
            return _webhook_response(
                "I could not verify your identity, so I did not access claim information. "
                "I can offer assistance from a human agent."
            )

        if tag == "claim_status":
            claim_id = _string_parameter(parameters.get("claim_id"))
            claim = data_store.get_claim(claim_id)
            if claim is None or claim.member_id != member.member_id:
                return _webhook_response(
                    "I could not find a claim associated with your verified account. "
                    "I can offer assistance from a human agent."
                )
            decision = data_store.get_decision(claim_id)
            if decision is None:
                return _webhook_response(
                    "The claim is not yet available in the local adjudication results."
                )
            reason_codes = ", ".join(reason.code for reason in decision.reason_codes)
            result = f"Claim status: {decision.status.value}."
            if reason_codes:
                result += f" Backend reason codes: {reason_codes}."
            return _webhook_response(result)

        if tag == "submit_appeal":
            claim_id = _string_parameter(parameters.get("claim_id"))
            claim = data_store.get_claim(claim_id)
            appeal_text = _string_parameter(parameters.get("appeal_text"))
            if claim is None or claim.member_id != member.member_id or not appeal_text:
                return _webhook_response(
                    "I could not submit the appeal. Please check the claim and "
                    "required information."
                )
            appeal = Appeal(
                appeal_id=f"SYN-APPEAL-{uuid4().hex[:12].upper()}",
                claim_id=claim_id,
                member_id=member.member_id,
                appeal_text=appeal_text,
            )
            data_store.save_appeal(appeal)
            return _webhook_response(
                "Your appeal information was received for review. "
                "This prototype does not determine appeal outcomes."
            )

        return _webhook_response(
            "I could not complete that request. I can offer a human handoff."
        )

    return app


def _verified_member(
    storage: Storage,
    member_id_value: object,
    date_of_birth_value: object,
) -> Member | None:
    """Resolve the member only when both synthetic identity fields match."""
    if not isinstance(member_id_value, str) or not isinstance(date_of_birth_value, str):
        return None
    try:
        date_of_birth = date.fromisoformat(date_of_birth_value)
    except ValueError:
        return None
    member = storage.get_member(member_id_value)
    if member is None or member.date_of_birth != date_of_birth:
        return None
    return member


def _verify_webhook_token(authorization: str | None, token: str | None) -> None:
    """Enforce an optional shared secret in local-mock or deployed CX mode."""
    if token is None:
        return
    supplied = (
        authorization.removeprefix("Bearer ").strip()
        if authorization is not None
        else ""
    )
    if not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Webhook authentication failed.")


def _string_parameter(value: object) -> str:
    """Return a trimmed string parameter or an empty value."""
    return value.strip() if isinstance(value, str) else ""


def _as_dict(value: object) -> dict[str, Any]:
    """Narrow unknown webhook input to a JSON object."""
    return value if isinstance(value, dict) else {}


def _webhook_response(message: str) -> dict[str, Any]:
    """Build the standard CX fulfillment response shape."""
    return {"fulfillmentResponse": {"messages": [{"text": {"text": [message]}}]}}


app = create_app()
