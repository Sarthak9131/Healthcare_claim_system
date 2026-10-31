"""Metric calculation tests independent from the longer evaluation run."""

from pathlib import Path

from evals.conversation_eval import run_conversation_evaluation
from evals.generate_dataset import EvaluationDataset, build_evaluation_dataset
from evals.run_eval import _decision_metrics, _failure_cases, _repeatability

from claims_processing_cx.models import Decision, RankedReasonCode


def _decision(claim_id: str, status: str, codes: list[str]) -> Decision:
    """Create a valid controlled outcome for metric tests."""
    return Decision(
        claim_id=claim_id,
        status=status,
        allowed_amount="100.00",
        paid_amount="80.00",
        member_responsibility="20.00",
        reason_codes=[
            RankedReasonCode(code=code, description="Synthetic test reason", rank=index)
            for index, code in enumerate(codes, start=1)
        ],
        explanation="Synthetic metric fixture.",
    )


def test_metrics_report_correct_accuracy_confusion_and_reasons() -> None:
    """A controlled correct result contributes to all matching metric cells."""
    case = build_evaluation_dataset().cases[0]
    single_case = EvaluationDataset(seed=1, cases=[case])
    decision = _decision(
        case.claim.claim_id,
        case.expected_status.value,
        case.expected_reason_codes,
    )
    decisions = {case.claim.claim_id: decision}

    metrics = _decision_metrics(
        single_case,
        decisions,
    )

    assert metrics["decision_accuracy"] == 1.0
    assert metrics["reason_code_exact_accuracy"] == 1.0
    assert metrics["confusion_matrix"]["rows_expected_columns_predicted"][
        case.expected_status.value
    ][case.expected_status.value] == 1
    assert _repeatability(
        single_case,
        [decisions, decisions, decisions],
    )["exact_consistency_rate"] == 1.0
    assert _failure_cases(
        single_case,
        decisions,
    ) == []


async def test_local_conversation_evaluation_scores_all_scripted_cases() -> None:
    """The CX simulator reports measured routing, escalation, privacy, and grounding."""
    data = build_evaluation_dataset()
    cases_path = Path(__file__).parents[1] / "cx_agent" / "conversation-cases.json"

    metrics = await run_conversation_evaluation(data, cases_path)

    assert metrics["dialogue_count"] == 6
    assert metrics["intent_routing_accuracy"] == 1.0
    assert metrics["escalation_correctness"] == 1.0
    assert metrics["no_phi_leakage_rate"] == 1.0
    assert metrics["denial_explanation_groundedness"] == 1.0
