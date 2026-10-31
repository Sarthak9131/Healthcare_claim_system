"""Typed domain models for synthetic claims processing."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DecisionStatus(StrEnum):
    """Supported claim adjudication outcomes."""

    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    DENIED = "DENIED"
    PENDED_FOR_REVIEW = "PENDED_FOR_REVIEW"


class ServiceLine(StrictModel):
    """A single billed service line."""

    line_id: str
    service_date: date | None = None
    procedure_code: str | None = None
    hcpcs_code: str | None = None
    diagnosis_codes: list[str] = Field(default_factory=list)
    units: int = Field(default=1, ge=1)
    billed_amount: Decimal = Field(default=Decimal("0.00"), ge=0)


class Member(StrictModel):
    """Synthetic member and coverage enrollment record."""

    member_id: str
    name: str
    date_of_birth: date
    zip_code: str
    policy_id: str
    coverage_start: date
    coverage_end: date | None = None
    secondary_payer_id: str | None = None


class Provider(StrictModel):
    """Synthetic provider record."""

    provider_id: str
    name: str
    npi: str
    zip_code: str
    in_network_policy_ids: list[str] = Field(default_factory=list)


class PolicyBenefits(StrictModel):
    """Benefit amounts and cost-sharing rules for one synthetic policy."""

    policy_id: str
    annual_deductible: Decimal = Field(ge=0)
    deductible_met: Decimal = Field(default=Decimal("0.00"), ge=0)
    coinsurance_rate: Decimal = Field(ge=0, le=1)
    out_of_network_coinsurance_rate: Decimal = Field(ge=0, le=1)
    out_of_pocket_maximum: Decimal = Field(ge=0)
    prior_authorization_codes: list[str] = Field(default_factory=list)
    covered_hcpcs_codes: list[str] = Field(default_factory=list)


class Policy(StrictModel):
    """A synthetic policy container associated with its benefits."""

    policy_id: str
    plan_name: str
    payer_id: str
    effective_date: date
    termination_date: date | None = None
    benefits: PolicyBenefits


class Claim(StrictModel):
    """A normalized synthetic claim; business-required fields may be absent for intake checks."""

    claim_id: str
    member_id: str | None = None
    provider_id: str | None = None
    received_date: date | None = None
    service_lines: list[ServiceLine] = Field(default_factory=list)
    billed_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    prior_authorization_id: str | None = None
    other_payer_paid: Decimal = Field(default=Decimal("0.00"), ge=0)
    diagnosis_codes: list[str] = Field(default_factory=list)
    scenario_tags: list[str] = Field(default_factory=list)
    source_format: str = "synthetic-837-json"


class EligibilityResult(StrictModel):
    """Eligibility and coverage response resembling a 271 result."""

    member_id: str
    policy_id: str
    eligible: bool
    coverage_start: date
    coverage_end: date | None = None
    network_status: str
    message: str


class CheckResult(StrictModel):
    """Validated output from an individual deterministic or judgment-based check."""

    check_name: str
    passed: bool
    reason_code: str | None = None
    details: str
    confidence: Decimal = Field(default=Decimal("1.0"), ge=0, le=1)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class RankedReasonCode(StrictModel):
    """A ranked claim outcome reason code with a plain description."""

    code: str
    description: str
    rank: int = Field(ge=1)


class Decision(StrictModel):
    """Final claim outcome with ranked reasons and a member-facing explanation."""

    claim_id: str
    status: DecisionStatus
    allowed_amount: Decimal = Field(ge=0)
    paid_amount: Decimal = Field(ge=0)
    member_responsibility: Decimal = Field(ge=0)
    reason_codes: list[RankedReasonCode] = Field(default_factory=list)
    explanation: str
    requires_human_review: bool = False


class SyntheticDataset(StrictModel):
    """Generated synthetic source records used by local tests and demos."""

    seed: int
    members: list[Member]
    providers: list[Provider]
    policies: list[Policy]
    claims: list[Claim]
