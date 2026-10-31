"""Seeded generator for strictly synthetic claims and coverage records."""

from datetime import date
from decimal import Decimal
from random import Random

from claims_processing_cx.models import (
    Claim,
    Member,
    Policy,
    PolicyBenefits,
    Provider,
    ServiceLine,
    SyntheticDataset,
)

_DIAGNOSES = ("J06.9", "M54.5", "I10", "E11.9")
_HCPCS = ("A0428", "J1885", "G0008")
_ILLUSTRATIVE_PROCEDURES = ("ILL-PROC-001", "ILL-PROC-002")
_NAMES = ("Alex Example", "Jordan Sample", "Casey Synthetic", "Morgan Demo")
_ZIP_CODES = ("00001", "00002", "00003", "00004")
_EDGE_CASES = (
    "duplicate",
    "expired_coverage",
    "out_of_network",
    "missing_prior_authorization",
    "diagnosis_procedure_mismatch",
    "unbundling",
    "coordination_of_benefits",
    "missing_required_fields",
)


def create_synthetic_dataset(seed: int = 17) -> SyntheticDataset:
    """Create reproducible synthetic records covering requested edge cases."""
    random = Random(seed)
    policy_ids = ("SYN-PLAN-001", "SYN-PLAN-002")
    policies = [
        Policy(
            policy_id=policy_id,
            plan_name=f"Synthetic Choice {index}",
            payer_id=f"SYN-PAYER-{index:03d}",
            effective_date=date(2020, 1, 1),
            benefits=PolicyBenefits(
                policy_id=policy_id,
                annual_deductible=Decimal("500.00"),
                deductible_met=Decimal("100.00"),
                coinsurance_rate=Decimal("0.20"),
                out_of_network_coinsurance_rate=Decimal("0.40"),
                out_of_pocket_maximum=Decimal("3000.00"),
                prior_authorization_codes=["J1885"],
                covered_hcpcs_codes=list(_HCPCS),
            ),
        )
        for index, policy_id in enumerate(policy_ids, start=1)
    ]
    members = [
        Member(
            member_id=f"SYN-MEMBER-{index:04d}",
            name=random.choice(_NAMES),
            date_of_birth=date(1980 + index, 1 + index % 12, 1 + index % 27),
            zip_code=random.choice(_ZIP_CODES),
            policy_id=policy_ids[(index - 1) % len(policy_ids)],
            coverage_start=date(2020, 1, 1),
            coverage_end=date(2024, 12, 31) if index == 2 else None,
            secondary_payer_id="SYN-SECONDARY-001" if index == 7 else None,
        )
        for index in range(1, 9)
    ]
    providers = [
        Provider(
            provider_id=f"SYN-PROVIDER-{index:03d}",
            name=f"Example Clinic {index}",
            npi=f"90000000{index:02d}",
            zip_code=_ZIP_CODES[(index - 1) % len(_ZIP_CODES)],
            in_network_policy_ids=(
                [] if index == 2 else [policy.policy_id for policy in policies]
            ),
        )
        for index in range(1, 5)
    ]

    claims: list[Claim] = []
    for index, tag in enumerate(_EDGE_CASES, start=1):
        member = members[(index - 1) % len(members)]
        provider = providers[1] if tag == "out_of_network" else providers[0]
        code = "J1885" if tag == "missing_prior_authorization" else random.choice(_HCPCS)
        diagnoses = ["J06.9"]
        if tag == "diagnosis_procedure_mismatch":
            diagnoses = ["M54.5"]
        lines = [
            ServiceLine(
                line_id=f"SYN-LINE-{index:04d}-1",
                service_date=date(2025, 4, 15),
                procedure_code=_ILLUSTRATIVE_PROCEDURES[0],
                hcpcs_code=code,
                diagnosis_codes=diagnoses,
                units=2 if tag == "unbundling" else 1,
                billed_amount=Decimal("150.00"),
            )
        ]
        if tag == "unbundling":
            lines.append(
                ServiceLine(
                    line_id=f"SYN-LINE-{index:04d}-2",
                    service_date=date(2025, 4, 15),
                    procedure_code=_ILLUSTRATIVE_PROCEDURES[0],
                    hcpcs_code=code,
                    diagnosis_codes=diagnoses,
                    billed_amount=Decimal("125.00"),
                )
            )
        claim = Claim(
            claim_id=f"SYN-CLAIM-{index:05d}",
            member_id=member.member_id,
            provider_id=provider.provider_id,
            received_date=date(2025, 5, 1),
            service_lines=lines,
            billed_amount=sum((line.billed_amount for line in lines), Decimal("0.00")),
            prior_authorization_id=(
                None if tag == "missing_prior_authorization" else "SYN-PA-0001"
            ),
            other_payer_paid=(
                Decimal("50.00")
                if tag == "coordination_of_benefits"
                else Decimal("0.00")
            ),
            diagnosis_codes=diagnoses,
            scenario_tags=[tag],
        )
        if tag == "missing_required_fields":
            claim = claim.model_copy(
                update={"member_id": None, "provider_id": None, "service_lines": []}
            )
        claims.append(claim)
        if tag == "duplicate":
            claims.append(
                claim.model_copy(
                    update={
                        "claim_id": "SYN-CLAIM-00009",
                        "scenario_tags": ["duplicate_copy"],
                    }
                )
            )

    return SyntheticDataset(
        seed=seed,
        members=members,
        providers=providers,
        policies=policies,
        claims=claims,
    )
