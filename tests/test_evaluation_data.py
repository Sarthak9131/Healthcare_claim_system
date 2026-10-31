"""Reproducibility and label-quality checks for the evaluation dataset."""

from collections import Counter

from evals.generate_dataset import build_evaluation_dataset

from claims_processing_cx.models import DecisionStatus


def test_evaluation_dataset_is_seeded_balanced_and_covers_edge_cases() -> None:
    """Expected labels and required synthetic scenarios are reproducible."""
    first = build_evaluation_dataset(seed=20261031)
    second = build_evaluation_dataset(seed=20261031)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert len(first.cases) == 128
    status_counts = Counter(case.expected_status for case in first.cases)
    assert status_counts == {
        DecisionStatus.PAID: 32,
        DecisionStatus.PARTIALLY_PAID: 32,
        DecisionStatus.DENIED: 32,
        DecisionStatus.PENDED_FOR_REVIEW: 32,
    }
    scenarios = {case.scenario for case in first.cases}
    assert {
        "duplicate",
        "expired_coverage",
        "partially_paid",
        "missing_prior_authorization",
        "diagnosis_procedure_mismatch",
        "unbundling",
        "coordination_of_benefits",
        "missing_required_fields",
        "low_confidence_documentation",
    } <= scenarios
    assert any(
        case.scenario == "partially_paid"
        and not case.provider.in_network_policy_ids
        for case in first.cases
    )


def test_evaluation_code_subset_excludes_cpt_fixture_codes() -> None:
    """Generated claims use only the small stated ICD/HCPCS/illustrative subsets."""
    dataset = build_evaluation_dataset()
    hcpcs_codes = {
        line.hcpcs_code
        for case in dataset.cases
        for line in case.claim.service_lines
    }
    procedure_codes = {
        line.procedure_code
        for case in dataset.cases
        for line in case.claim.service_lines
    }

    assert hcpcs_codes <= {"A0428", "G0008", "J1885"}
    assert procedure_codes <= {"ILL-PROC-001", "ILL-PROC-002"}
    assert "99213" not in hcpcs_codes
