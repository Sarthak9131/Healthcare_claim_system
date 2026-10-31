"""Sequential parsing, normalization, and required-field validation."""

from typing import Any

from pydantic import ValidationError

from claims_processing_cx.models import Claim, ServiceLine

_REQUIRED_CODES = ("member_id", "provider_id", "received_date")


class IntakeError(ValueError):
    """Input could not be safely represented as a normalized claim."""


def normalize_claim(payload: dict[str, Any]) -> Claim:
    """Parse synthetic 837-style JSON and validate required business fields."""
    claim = Claim.model_validate(payload)
    if not claim.claim_id:
        raise IntakeError("claim_id is required")
    missing = [field for field in _REQUIRED_CODES if not getattr(claim, field)]
    if not claim.service_lines:
        missing.append("service_lines")
    for index, line in enumerate(claim.service_lines):
        if not line.line_id:
            missing.append(f"service_lines[{index}].line_id")
        if not line.service_date:
            missing.append(f"service_lines[{index}].service_date")
        if not line.procedure_code:
            missing.append(f"service_lines[{index}].procedure_code")
        if not line.hcpcs_code:
            missing.append(f"service_lines[{index}].hcpcs_code")
        if not (line.diagnosis_codes or claim.diagnosis_codes):
            missing.append(f"service_lines[{index}].diagnosis_codes")
    if missing:
        raise IntakeError(f"Missing required fields: {', '.join(missing)}")

    normalized_lines = [
        ServiceLine(
            **{
                **line.model_dump(),
                "procedure_code": line.procedure_code.upper() if line.procedure_code else None,
                "hcpcs_code": line.hcpcs_code.upper() if line.hcpcs_code else None,
                "diagnosis_codes": [code.upper() for code in line.diagnosis_codes],
            }
        )
        for line in claim.service_lines
    ]
    claim_data = claim.model_dump()
    claim_data["member_id"] = claim.member_id.upper() if claim.member_id else None
    claim_data["provider_id"] = claim.provider_id.upper() if claim.provider_id else None
    claim_data["diagnosis_codes"] = [code.upper() for code in claim.diagnosis_codes]
    claim_data["service_lines"] = normalized_lines
    if claim.billed_amount == 0:
        claim_data["billed_amount"] = sum(
            (line.billed_amount for line in normalized_lines), start=claim.billed_amount
        )
    return Claim.model_validate(claim_data)


def intake_error_text(error: Exception) -> str:
    """Return a bounded, non-payload-bearing intake error for audit use."""
    if isinstance(error, ValidationError):
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"])
                for item in error.errors(include_input=False)
            }
        )
        return f"Invalid claim schema at: {', '.join(fields)}"
    if isinstance(error, IntakeError):
        return str(error)
    return f"Invalid claim input ({type(error).__name__})"
