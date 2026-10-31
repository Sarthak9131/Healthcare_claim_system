"""Paired synthetic proxy test for outcome disagreement."""

import asyncio
import tempfile
from pathlib import Path

from claims_processing_cx.audit import AuditTrail
from claims_processing_cx.llm import StubLLMClient
from claims_processing_cx.models import Claim, Decision, StrictModel, SyntheticDataset
from claims_processing_cx.pipeline import ClaimsPipeline
from claims_processing_cx.storage import InMemoryStorage


class FairnessPairResult(StrictModel):
    """Outcome comparison for two records that differ only by name and ZIP."""

    claim_id: str
    baseline_status: str
    paired_status: str
    baseline_reason_codes: list[str]
    paired_reason_codes: list[str]
    decision_disagreement: bool


async def compare_demographic_pair(
    claim: Claim,
    dataset: SyntheticDataset,
    *,
    alternate_name: str = "Taylor Synthetic",
    alternate_zip_code: str = "99999",
) -> FairnessPairResult:
    """Adjudicate identical claims with only the linked member name and ZIP changed."""
    if claim.member_id is None:
        raise ValueError("A member-linked claim is required for the paired comparison.")
    baseline_member = next(
        (member for member in dataset.members if member.member_id == claim.member_id),
        None,
    )
    if baseline_member is None:
        raise ValueError("The claim's synthetic member is missing from the dataset.")
    paired_member = baseline_member.model_copy(
        update={"name": alternate_name, "zip_code": alternate_zip_code}
    )
    paired_dataset = dataset.model_copy(
        update={
            "members": [
                paired_member if member.member_id == paired_member.member_id else member
                for member in dataset.members
            ]
        }
    )

    with tempfile.TemporaryDirectory(prefix="claims-fairness-") as directory:
        root = Path(directory)
        baseline = await _run_claim(claim, dataset, root / "baseline")
        paired = await _run_claim(claim, paired_dataset, root / "paired")

    baseline_codes = [item.code for item in baseline.reason_codes]
    paired_codes = [item.code for item in paired.reason_codes]
    disagreement = (
        baseline.status != paired.status
        or baseline_codes != paired_codes
        or baseline.allowed_amount != paired.allowed_amount
        or baseline.paid_amount != paired.paid_amount
        or baseline.member_responsibility != paired.member_responsibility
    )
    return FairnessPairResult(
        claim_id=claim.claim_id,
        baseline_status=baseline.status.value,
        paired_status=paired.status.value,
        baseline_reason_codes=baseline_codes,
        paired_reason_codes=paired_codes,
        decision_disagreement=disagreement,
    )


async def _run_claim(claim: Claim, dataset: SyntheticDataset, audit_dir: Path) -> Decision:
    """Run one pair member through a fresh deterministic local pipeline."""
    storage = InMemoryStorage(dataset)
    pipeline = ClaimsPipeline(
        storage,
        StubLLMClient(),
        AuditTrail(audit_dir),
    )
    return await pipeline.adjudicate(claim.model_dump(mode="json"))


def compare_demographic_pair_sync(
    claim: Claim,
    dataset: SyntheticDataset,
    *,
    alternate_name: str = "Taylor Synthetic",
    alternate_zip_code: str = "99999",
) -> FairnessPairResult:
    """Synchronous wrapper for command-line evaluation scripts."""
    return asyncio.run(
        compare_demographic_pair(
            claim,
            dataset,
            alternate_name=alternate_name,
            alternate_zip_code=alternate_zip_code,
        )
    )
