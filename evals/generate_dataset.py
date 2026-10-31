"""Generate a balanced, seeded, synthetic adjudication evaluation set."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from random import Random

from pydantic import Field

from claims_processing_cx.models import (
    Claim,
    DecisionStatus,
    Member,
    Policy,
    PolicyBenefits,
    Provider,
    ServiceLine,
    StrictModel,
)

ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "synthetic" / "evaluation_claims.json"
SERVICE_DATE = "2025-04-15"


class EvaluationCase(StrictModel):
    """One claim plus an independent expected outcome and reason-code label."""

    scenario: str
    claim: Claim
    member: Member
    provider: Provider
    policy: Policy
    expected_status: DecisionStatus
    expected_reason_codes: list[str] = Field(default_factory=list)


class EvaluationDataset(StrictModel):
    """Versioned expected labels for repeatable offline evaluation."""

    seed: int
    cases: list[EvaluationCase]


_GROUPS: tuple[tuple[str, int, DecisionStatus, list[str]], ...] = (
    ("paid", 32, DecisionStatus.PAID, []),
    ("partially_paid", 32, DecisionStatus.PARTIALLY_PAID, ["PR-1", "PR-2"]),
    ("expired_coverage", 8, DecisionStatus.DENIED, ["CO-27"]),
    ("missing_prior_authorization", 8, DecisionStatus.DENIED, ["CO-197"]),
    ("diagnosis_procedure_mismatch", 8, DecisionStatus.DENIED, ["CO-50"]),
    ("duplicate", 8, DecisionStatus.DENIED, ["CO-18"]),
    ("high_dollar", 6, DecisionStatus.PENDED_FOR_REVIEW, ["FWA-REVIEW", "SYN-HIGH-DOLLAR"]),
    ("coordination_of_benefits", 6, DecisionStatus.PENDED_FOR_REVIEW, ["COB-INFO"]),
    ("unbundling", 6, DecisionStatus.PENDED_FOR_REVIEW, ["CO-97", "FWA-REVIEW"]),
    ("missing_required_fields", 6, DecisionStatus.PENDED_FOR_REVIEW, ["SCHEMA-001"]),
    ("low_confidence_documentation", 8, DecisionStatus.PENDED_FOR_REVIEW, ["DOC-REVIEW"]),
)


def build_evaluation_dataset(seed: int = 20261031) -> EvaluationDataset:
    """Build 128 deterministic cases balanced across four expected outcomes."""
    random = Random(seed)
    policy_ids = [f"SYN-EVAL-PLAN-{index:04d}" for index in range(1, 129)]
    networked_policy_ids = list(policy_ids)
    providers = [
        Provider(
            provider_id="SYN-EVAL-PROVIDER-001",
            name="Evaluation Clinic One",
            npi="9000000001",
            zip_code="00011",
            in_network_policy_ids=networked_policy_ids,
        ),
        Provider(
            provider_id="SYN-EVAL-PROVIDER-002",
            name="Evaluation Clinic Two",
            npi="9000000002",
            zip_code="00012",
            in_network_policy_ids=[],
        ),
    ]
    cases: list[EvaluationCase] = []
    index = 0
    for scenario, count, expected_status, expected_reasons in _GROUPS:
        for offset in range(count):
            index += 1
            policy_id = policy_ids[index - 1]
            member_id = f"SYN-EVAL-MEMBER-{index:04d}"
            claim_id = f"SYN-EVAL-CLAIM-{index:04d}"
            high_dollar = scenario == "high_dollar"
            prior_auth_case = scenario == "missing_prior_authorization"
            covered_codes = ["J1885"] if prior_auth_case else []
            deductible = "0.00" if scenario == "paid" else "500.00"
            deductible_met = "0.00" if scenario == "paid" else "100.00"
            coinsurance = "0.00" if scenario == "paid" else "0.20"
            benefits = PolicyBenefits(
                policy_id=policy_id,
                annual_deductible=deductible,
                deductible_met=deductible_met,
                coinsurance_rate=coinsurance,
                out_of_network_coinsurance_rate="0.40",
                out_of_pocket_maximum="3000.00",
                prior_authorization_codes=covered_codes,
                covered_hcpcs_codes=["A0428", "G0008", "J1885"],
            )
            policy = Policy(
                policy_id=policy_id,
                plan_name="Synthetic evaluation plan",
                payer_id=f"SYN-EVAL-PAYER-{index:04d}",
                effective_date="2020-01-01",
                benefits=benefits,
            )
            member = Member(
                member_id=member_id,
                name=f"Evaluation Member {index:04d}",
                date_of_birth=f"{1980 + index % 30:04d}-01-{1 + index % 27:02d}",
                zip_code=f"{index % 90000 + 10000:05d}",
                policy_id=policy_id,
                coverage_start="2020-01-01",
                coverage_end="2024-12-31" if scenario == "expired_coverage" else None,
                secondary_payer_id=(
                    f"SYN-EVAL-SECONDARY-{index:04d}"
                    if scenario == "coordination_of_benefits"
                    else None
                ),
            )
            provider = (
                providers[1]
                if scenario == "partially_paid" and offset % 4 == 0
                else providers[0]
            )
            diagnosis = (
                "M54.5" if scenario == "diagnosis_procedure_mismatch" else "J06.9"
            )
            hcpcs = "J1885" if prior_auth_case else random.choice(["A0428", "G0008"])
            amount = (
                "12000.00"
                if high_dollar
                else f"{random.randint(700, 1800)}.00"
                if scenario == "partially_paid"
                else "1000.00"
            )
            lines = [
                ServiceLine(
                    line_id=f"SYN-EVAL-LINE-{index:04d}-1",
                    service_date=SERVICE_DATE,
                    procedure_code="ILL-PROC-001",
                    hcpcs_code=hcpcs,
                    diagnosis_codes=[diagnosis],
                    units=1,
                    billed_amount=amount,
                )
            ]
            claim = Claim(
                claim_id=claim_id,
                member_id=member_id,
                provider_id=provider.provider_id,
                received_date="2025-05-01",
                service_lines=lines,
                billed_amount=amount,
                prior_authorization_id=None if prior_auth_case else f"SYN-EVAL-PA-{index:04d}",
                other_payer_paid=(
                    "0.00" if scenario == "coordination_of_benefits" else "0.00"
                ),
                diagnosis_codes=[diagnosis],
                duplicate_of_claim_id=(
                    f"SYN-EVAL-CLAIM-{index - 1:04d}" if scenario == "duplicate" else None
                ),
                clinical_documentation=(
                    "Synthetic note with ambiguous documentation."
                    if scenario == "low_confidence_documentation"
                    else None
                ),
                scenario_tags=[scenario],
            )
            if scenario == "unbundling":
                second_line = lines[0].model_copy(
                    update={
                        "line_id": f"SYN-EVAL-LINE-{index:04d}-2",
                        "billed_amount": Decimal("250.00"),
                    }
                )
                claim = claim.model_copy(
                    update={
                        "service_lines": [lines[0], second_line],
                        "billed_amount": Decimal("1250.00"),
                    }
                )
            if scenario == "missing_required_fields":
                claim = claim.model_copy(
                    update={"member_id": None, "provider_id": None, "service_lines": []}
                )
            cases.append(
                EvaluationCase(
                    scenario=scenario,
                    claim=claim,
                    member=member,
                    provider=provider,
                    policy=policy,
                    expected_status=expected_status,
                    expected_reason_codes=expected_reasons,
                )
            )
    return EvaluationDataset(seed=seed, cases=cases)


def write_dataset(output_path: Path, seed: int = 20261031) -> EvaluationDataset:
    """Generate and write the labeled JSON dataset."""
    dataset = build_evaluation_dataset(seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dataset.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return dataset


def main() -> None:
    """CLI entry point for deterministic label generation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    dataset = write_dataset(arguments.output, arguments.seed)
    print(f"Wrote {len(dataset.cases)} synthetic labeled claims to {arguments.output}")


if __name__ == "__main__":
    main()
