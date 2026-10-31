"""Backend pipeline and FastAPI contract tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from claims_processing_cx.api import create_app
from claims_processing_cx.audit import AuditTrail
from claims_processing_cx.llm import StubLLMClient
from claims_processing_cx.models import DecisionStatus
from claims_processing_cx.pipeline import ClaimsPipeline
from claims_processing_cx.storage import InMemoryStorage
from claims_processing_cx.synthetic import create_synthetic_dataset


def _runtime(tmp_path: Path) -> tuple[InMemoryStorage, ClaimsPipeline]:
    dataset = create_synthetic_dataset()
    storage = InMemoryStorage(dataset)
    pipeline = ClaimsPipeline(
        storage,
        StubLLMClient(),
        AuditTrail(tmp_path / "audit"),
    )
    return storage, pipeline


async def test_pipeline_decisions_and_audit_trail(tmp_path: Path) -> None:
    """Exact failures deny; configured anomalies and schema failures pend."""
    storage, pipeline = _runtime(tmp_path)
    dataset = create_synthetic_dataset()
    claims_by_tag = {
        tag: next(claim for claim in dataset.claims if tag in claim.scenario_tags)
        for tag in (
            "duplicate",
            "expired_coverage",
            "missing_prior_authorization",
            "unbundling",
            "missing_required_fields",
        )
    }

    expired = await pipeline.adjudicate(
        claims_by_tag["expired_coverage"].model_dump(mode="json")
    )
    no_auth = await pipeline.adjudicate(
        claims_by_tag["missing_prior_authorization"].model_dump(mode="json")
    )
    unbundled = await pipeline.adjudicate(claims_by_tag["unbundling"].model_dump(mode="json"))
    missing = await pipeline.adjudicate(
        claims_by_tag["missing_required_fields"].model_dump(mode="json")
    )

    assert expired.status == DecisionStatus.DENIED
    assert "CO-27" in [reason.code for reason in expired.reason_codes]
    assert no_auth.status == DecisionStatus.DENIED
    assert "CO-197" in [reason.code for reason in no_auth.reason_codes]
    assert unbundled.status == DecisionStatus.PENDED_FOR_REVIEW
    assert missing.status == DecisionStatus.PENDED_FOR_REVIEW
    assert storage.get_decision(missing.claim_id) == missing
    assert list((tmp_path / "audit").glob("*.json"))


async def test_check_schema_failure_retries_then_pends(tmp_path: Path) -> None:
    """Invalid agent/check output is retried and cannot produce a payment."""
    storage, pipeline = _runtime(tmp_path)
    claim = storage.get_claim("SYN-CLAIM-00001")
    assert claim is not None

    async def invalid_check(_: object) -> dict[str, object]:
        return {"passed": True, "details": "missing the required check name"}

    pipeline.checks.fraud_waste_abuse = invalid_check  # type: ignore[method-assign]
    decision = await pipeline.adjudicate(claim.model_dump(mode="json"))

    assert decision.status == DecisionStatus.PENDED_FOR_REVIEW
    events = pipeline.audit_trail.events_for(claim.claim_id)
    failure = next(event for event in events if event.step == "fraud_waste_abuse")
    assert len(failure.errors) == 3
    assert "CHECK-FAILURE" in [reason.code for reason in decision.reason_codes]


def test_http_endpoints_and_identity_verified_webhook(tmp_path: Path) -> None:
    """Exercise adjudication, status, appeals, queue, and verified CX lookup."""
    dataset = create_synthetic_dataset()
    storage = InMemoryStorage(dataset)
    app = create_app(storage, audit_directory=tmp_path / "audit")
    client = TestClient(app)
    claim = next(item for item in dataset.claims if "expired_coverage" in item.scenario_tags)
    member = storage.get_member(claim.member_id or "")
    assert member is not None

    adjudication = client.post("/adjudicate", json=claim.model_dump(mode="json"))
    assert adjudication.status_code == 200
    assert adjudication.json()["status"] == DecisionStatus.DENIED.value

    status = client.get(f"/claims/{claim.claim_id}/status")
    assert status.status_code == 200
    assert status.json()["claim_id"] == claim.claim_id

    appeal = client.post(
        "/appeals",
        json={
            "claim_id": claim.claim_id,
            "member_id": member.member_id,
            "appeal_text": "Synthetic appeal statement.",
        },
    )
    assert appeal.status_code == 200

    wrong_identity = client.post(
        "/webhook",
        json={
            "fulfillmentInfo": {"tag": "claim_status"},
            "sessionInfo": {
                "parameters": {
                    "member_id": member.member_id,
                    "date_of_birth": "1900-01-01",
                    "claim_id": claim.claim_id,
                }
            },
        },
    )
    response_text = wrong_identity.json()["fulfillmentResponse"]["messages"][0]["text"]["text"][0]
    assert "could not verify" in response_text
    assert claim.claim_id not in response_text

    verified = client.post(
        "/webhook",
        json={
            "fulfillmentInfo": {"tag": "claim_status"},
            "sessionInfo": {
                "parameters": {
                    "member_id": member.member_id,
                    "date_of_birth": member.date_of_birth.isoformat(),
                    "claim_id": claim.claim_id,
                }
            },
        },
    )
    verified_text = verified.json()["fulfillmentResponse"]["messages"][0]["text"]["text"][0]
    assert "DENIED" in verified_text
    assert member.member_id not in verified_text
    assert member.date_of_birth.isoformat() not in verified_text

    queue = client.get("/review-queue")
    assert queue.status_code == 200
    assert client.get("/health").json()["mode"] == "mock"


def test_webhook_token_is_enforced_when_configured(tmp_path: Path) -> None:
    """A configured bearer token protects the webhook endpoint."""
    app = create_app(
        InMemoryStorage(create_synthetic_dataset()),
        audit_directory=tmp_path / "audit",
        webhook_token="local-test-token",
    )
    client = TestClient(app)

    unauthorized = client.post("/webhook", json={})
    authorized = client.post(
        "/webhook",
        json={},
        headers={"Authorization": "Bearer local-test-token"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
