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

    @app.post("/webhook")
    async def dialogflow_webhook(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Handle the standard Dialogflow CX webhook request in local mode."""
        if configured_token is not None:
            supplied = (
                authorization.removeprefix("Bearer ").strip()
                if authorization is not None
                else ""
            )
            if not secrets.compare_digest(supplied, configured_token):
                raise HTTPException(status_code=401, detail="Webhook authentication failed.")

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
                "I could not verify your identity, so I did not access claim information."
            )

        if tag == "claim_status":
            claim_id = _string_parameter(parameters.get("claim_id"))
            claim = data_store.get_claim(claim_id)
            if claim is None or claim.member_id != member.member_id:
                return _webhook_response(
                    "I could not find a claim associated with your verified account."
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
            "I could not complete that request. I can help with claim status or appeal intake."
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
