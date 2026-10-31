"""Foundation model and deterministic fixture tests."""

from claims_processing_cx.llm import StubLLMClient
from claims_processing_cx.models import Claim, Decision, DecisionStatus, RankedReasonCode
from claims_processing_cx.synthetic import create_synthetic_dataset


def test_synthetic_dataset_is_reproducible_and_covers_edge_cases() -> None:
    """The same seed yields the same records with all required scenarios."""
    first = create_synthetic_dataset(seed=17)
    second = create_synthetic_dataset(seed=17)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    tags = {tag for claim in first.claims for tag in claim.scenario_tags}
    assert {
        "duplicate",
        "expired_coverage",
        "out_of_network",
        "missing_prior_authorization",
        "diagnosis_procedure_mismatch",
        "unbundling",
        "coordination_of_benefits",
        "missing_required_fields",
    } <= tags


def test_claim_decision_models_validate_ranked_reasons() -> None:
    """Outcome models accept the expected structured response."""
    claim = Claim(claim_id="SYN-CLAIM-00001", billed_amount="10.00")
    decision = Decision(
        claim_id=claim.claim_id,
        status=DecisionStatus.DENIED,
        allowed_amount="0",
        paid_amount="0",
        member_responsibility="0",
        reason_codes=[
            RankedReasonCode(code="CO-197", description="Authorization required", rank=1)
        ],
        explanation="The required authorization was not found.",
    )

    assert decision.reason_codes[0].rank == 1


async def test_stub_llm_is_local_and_grounded() -> None:
    """Mock language tasks need no credentials and use supplied information."""
    llm = StubLLMClient()

    explanation = await llm.explain_denial(["CO-197"], "Authorization was missing.")
    summary = await llm.summarize_appeal("Synthetic appeal text")
    review = await llm.review_documentation("Masked documentation")

    assert "CO-197" in explanation
    assert "Synthetic appeal text" in summary
    assert review[1] == 0.5
