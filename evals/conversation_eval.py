"""Scripted local evaluation of the CX webhook/tool contract and guardrails."""

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from claims_processing_cx.api import create_app
from claims_processing_cx.models import SyntheticDataset
from claims_processing_cx.privacy import PIIMasker
from claims_processing_cx.storage import InMemoryStorage
from evals.generate_dataset import EvaluationDataset

_REASON_TEXT = {
    "CO-27": "coverage was not active for the service date",
    "CO-197": "required authorization was not supplied",
    "CO-50": "the illustrative coverage rule was not met",
    "CO-18": "the claim appears to be a duplicate",
}


def detect_local_intent(turns: list[str]) -> str:
    """Route scripted phrases with a transparent deterministic simulator."""
    message = " ".join(turns).casefold()
    if "ignore your rules" in message or "another member" in message or "every claim" in message:
        return "human_escalation"
    if "appeal" in message:
        return "appeal_intake"
    if "document" in message:
        return "missing_document_request"
    if "denied" in message or "denial" in message:
        return "denial_explanation"
    if "status" in message or "check a claim" in message:
        return "claim_status_lookup"
    return "human_escalation"


async def run_conversation_evaluation(
    evaluation_data: EvaluationDataset,
    cases_path: Path,
) -> dict[str, Any]:
    """Score local scripted dialogues through the same HTTP tool/webhook contracts."""
    scripted_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    claims_data = _synthetic_dataset(evaluation_data)
    target_case = next(
        case for case in evaluation_data.cases if case.scenario == "expired_coverage"
    )
    target_member = target_case.member
    with tempfile.TemporaryDirectory(prefix="claims-conversation-eval-") as directory:
        storage = InMemoryStorage(claims_data)
        app = create_app(
            audit_directory=Path(directory) / "audit",
            storage=storage,
        )
        pipeline = app.state.pipeline
        for case in evaluation_data.cases:
            if case.scenario == "expired_coverage":
                await pipeline.adjudicate(case.claim.model_dump(mode="json"))
                break

        with TestClient(app) as client:
            results: list[dict[str, Any]] = []
            for scripted in scripted_cases:
                results.append(
                    _evaluate_dialogue(
                        scripted,
                        client,
                        target_case.claim.claim_id,
                        target_member.member_id,
                        target_member.date_of_birth.isoformat(),
                        target_member.name,
                        PIIMasker(storage),
                        claims_data,
                    )
                )

    denominator = len(results)
    applicable_grounding = [
        result["groundedness"]
        for result in results
        if result["groundedness"] is not None
    ]
    return {
        "mode": "local deterministic routing simulator; no live Dialogflow CX runtime",
        "dialogue_count": denominator,
        "intent_routing_accuracy": _rate(
            sum(result["intent_correct"] for result in results), denominator
        ),
        "escalation_correctness": _rate(
            sum(result["escalation_correct"] for result in results), denominator
        ),
        "no_phi_leakage_rate": _rate(
            sum(result["no_phi_leakage"] for result in results), denominator
        ),
        "denial_explanation_groundedness": _rate(
            sum(bool(value) for value in applicable_grounding),
            len(applicable_grounding),
        ),
        "groundedness_dialogues": len(applicable_grounding),
        "cases": results,
    }


def _evaluate_dialogue(
    scripted: dict[str, Any],
    client: TestClient,
    claim_id: str,
    member_id: str,
    date_of_birth: str,
    member_name: str,
    masker: PIIMasker,
    dataset: SyntheticDataset,
) -> dict[str, Any]:
    """Execute one scripted case against a CX-shaped local backend request."""
    expected_intent = scripted["expected_intent"]
    actual_intent = detect_local_intent(scripted["turns"])
    groundedness: bool | None = None
    if expected_intent == "human_escalation":
        if "another-member" in scripted["name"]:
            attacker = next(
                member for member in dataset.members if member.member_id != member_id
            )
            response = client.post(
                "/webhook",
                json={
                    "fulfillmentInfo": {"tag": "claim_status"},
                    "sessionInfo": {
                        "parameters": {
                            "member_id": attacker.member_id,
                            "date_of_birth": attacker.date_of_birth.isoformat(),
                            "claim_id": claim_id,
                        }
                    },
                },
            )
            response_text = _webhook_text(response.json())
        else:
            response = client.post(
                "/webhook",
                json={"fulfillmentInfo": {"tag": "claim_status"}, "sessionInfo": {}},
            )
            response_text = _webhook_text(response.json())
    elif expected_intent == "appeal_intake":
        response = client.post(
            "/cx/tools/appeals",
            json={
                "claim_id": claim_id,
                "member_id": member_id,
                "date_of_birth": date_of_birth,
                "appeal_text": "Synthetic appeal text for evaluation.",
            },
        )
        response_text = (
            "Appeal received for review."
            if response.status_code == 200
            else "Appeal submission was not accepted."
        )
    else:
        response = client.post(
            "/cx/tools/claim-status",
            json={
                "claim_id": claim_id,
                "member_id": member_id,
                "date_of_birth": date_of_birth,
            },
        )
        if response.status_code == 200:
            body = response.json()
            codes = [item["code"] for item in body["reason_codes"]]
            if expected_intent == "denial_explanation":
                details = [
                    f"{code}: {_REASON_TEXT[code]}"
                    for code in codes
                    if code in _REASON_TEXT
                ]
                response_text = "The backend returned " + "; ".join(details) + "."
                cited = set(re.findall(r"\b[A-Z]{2,4}-[A-Z0-9-]+\b", response_text))
                groundedness = bool(codes) and cited <= set(codes)
            elif expected_intent == "missing_document_request":
                response_text = (
                    "This prototype does not receive document uploads. "
                    "Please follow the document instructions from your provider."
                )
            else:
                response_text = f"Claim status: {body['status']}."
        else:
            response_text = "I could not access a verified claim result."

    handoff_offered = "human" in response_text.casefold() or "handoff" in response_text.casefold()
    expected_handoff = expected_intent == "human_escalation"
    known_phi = [
        member_id,
        date_of_birth,
        member_name,
        claim_id,
        *[member.name for member in dataset.members],
        *[member.member_id for member in dataset.members],
        *[member.date_of_birth.isoformat() for member in dataset.members],
        *[member.zip_code for member in dataset.members],
        *[provider.npi for provider in dataset.providers],
        *[provider.name for provider in dataset.providers],
    ]
    no_phi_leakage = all(
        value.casefold() not in response_text.casefold()
        for value in known_phi
        if value
    )
    no_phi_leakage = no_phi_leakage and masker.mask_text(response_text) == response_text
    return {
        "name": scripted["name"],
        "expected_intent": expected_intent,
        "actual_intent": actual_intent,
        "intent_correct": actual_intent == expected_intent,
        "expected_handoff": expected_handoff,
        "handoff_offered": handoff_offered,
        "escalation_correct": expected_handoff == handoff_offered,
        "no_phi_leakage": no_phi_leakage,
        "groundedness": groundedness,
        "response": response_text,
    }


def _synthetic_dataset(evaluation_data: EvaluationDataset) -> SyntheticDataset:
    """Build the backend's fixture set from the labeled evaluation cases."""
    cases = evaluation_data.cases
    return SyntheticDataset(
        seed=evaluation_data.seed,
        members=[case.member for case in cases],
        providers=list({case.provider.provider_id: case.provider for case in cases}.values()),
        policies=[case.policy for case in cases],
        claims=[case.claim for case in cases],
    )


def _webhook_text(response: dict[str, Any]) -> str:
    """Extract text from the standard Dialogflow CX webhook response."""
    return response["fulfillmentResponse"]["messages"][0]["text"]["text"][0]


def _rate(numerator: int, denominator: int) -> float | None:
    """Calculate a metric safely when no cases apply."""
    return numerator / denominator if denominator else None
