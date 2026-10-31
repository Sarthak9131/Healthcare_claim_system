"""Storage interface and in-memory implementation."""

from typing import Protocol

from claims_processing_cx.models import (
    Appeal,
    Claim,
    Decision,
    Member,
    Policy,
    Provider,
    SyntheticDataset,
)


class Storage(Protocol):
    """Persistence operations needed by the pipeline and API."""

    def get_claim(self, claim_id: str) -> Claim | None:
        """Return a known claim."""

    def get_member(self, member_id: str) -> Member | None:
        """Return a known synthetic member."""

    def get_provider(self, provider_id: str) -> Provider | None:
        """Return a known synthetic provider."""

    def get_policy(self, policy_id: str) -> Policy | None:
        """Return a known synthetic policy."""

    def list_claims(self) -> list[Claim]:
        """Return claims used for duplicate detection."""

    def save_claim(self, claim: Claim) -> None:
        """Persist a normalized claim."""

    def save_decision(self, decision: Decision) -> None:
        """Persist a final decision."""

    def get_decision(self, claim_id: str) -> Decision | None:
        """Return the latest decision for a claim."""

    def list_decisions(self) -> list[Decision]:
        """Return decisions for reviewer queue filtering."""

    def save_appeal(self, appeal: Appeal) -> None:
        """Persist a received appeal."""


class InMemoryStorage:
    """Process-local storage suitable for tests and demonstrations only."""

    def __init__(self, dataset: SyntheticDataset) -> None:
        self.claims = {claim.claim_id: claim for claim in dataset.claims}
        self.members = {member.member_id: member for member in dataset.members}
        self.providers = {provider.provider_id: provider for provider in dataset.providers}
        self.policies = {policy.policy_id: policy for policy in dataset.policies}
        self.decisions: dict[str, Decision] = {}
        self.appeals: dict[str, Appeal] = {}

    def get_claim(self, claim_id: str) -> Claim | None:
        """Return a known claim."""
        return self.claims.get(claim_id)

    def get_member(self, member_id: str) -> Member | None:
        """Return a known synthetic member."""
        return self.members.get(member_id)

    def get_provider(self, provider_id: str) -> Provider | None:
        """Return a known synthetic provider."""
        return self.providers.get(provider_id)

    def get_policy(self, policy_id: str) -> Policy | None:
        """Return a known synthetic policy."""
        return self.policies.get(policy_id)

    def list_claims(self) -> list[Claim]:
        """Return claims used for duplicate detection."""
        return list(self.claims.values())

    def save_claim(self, claim: Claim) -> None:
        """Persist a normalized claim."""
        self.claims[claim.claim_id] = claim

    def save_decision(self, decision: Decision) -> None:
        """Persist a final decision and the claim if it is new."""
        self.decisions[decision.claim_id] = decision

    def get_decision(self, claim_id: str) -> Decision | None:
        """Return the latest decision for a claim."""
        return self.decisions.get(claim_id)

    def list_decisions(self) -> list[Decision]:
        """Return all persisted decisions."""
        return list(self.decisions.values())

    def save_appeal(self, appeal: Appeal) -> None:
        """Persist a received appeal."""
        self.appeals[appeal.appeal_id] = appeal
