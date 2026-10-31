"""PII masking for model inputs and structured audit records."""

import re
from typing import Any

from claims_processing_cx.storage import Storage

_PATTERNS = (
    (re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b"), "[SSN]"),
    (
        re.compile(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            re.IGNORECASE,
        ),
        "[EMAIL]",
    ),
    (
        re.compile(
            r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
        ),
        "[PHONE]",
    ),
    (
        re.compile(r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
        "[DATE]",
    ),
    (
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Za-z0-9.'#-]+\s+){0,5}"
            r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way)\b",
            re.IGNORECASE,
        ),
        "[ADDRESS]",
    ),
    (
        re.compile(
            r"\b(?:member|subscriber|patient)\s*(?:id|number|#)\s*[:#]?\s*\S+",
            re.IGNORECASE,
        ),
        "[MEMBER_ID]",
    ),
)

_SENSITIVE_KEYS = re.compile(
    r"(?:^|_)(?:name|ssn|social_security|member_id|subscriber_id|patient_id|"
    r"date_of_birth|dob|address|phone|telephone|email|zip_code|postal_code|npi)(?:$|_)",
    re.IGNORECASE,
)


class PIIMasker:
    """Redact common identifiers and known synthetic record values."""

    def __init__(self, storage: Storage | None = None) -> None:
        self._known_values: set[str] = set()
        if storage is not None:
            for member in storage.list_members():
                self._known_values.update(
                    {
                        member.name,
                        member.member_id,
                        member.date_of_birth.isoformat(),
                        member.date_of_birth.strftime("%m/%d/%Y"),
                        member.zip_code,
                    }
                )
            for provider in storage.list_providers():
                self._known_values.update(
                    {provider.name, provider.provider_id, provider.npi, provider.zip_code}
                )
        self._known_values = {value for value in self._known_values if value}

    def mask_text(self, value: str) -> str:
        """Mask known values and common PII patterns in arbitrary text."""
        masked = value
        for known_value in sorted(self._known_values, key=len, reverse=True):
            masked = re.sub(
                re.escape(known_value),
                "[REDACTED]",
                masked,
                flags=re.IGNORECASE,
            )
        for pattern, replacement in _PATTERNS:
            masked = pattern.sub(replacement, masked)
        return masked

    def mask_value(self, value: Any, key: str | None = None) -> Any:
        """Recursively mask structured data before logs or model calls."""
        if key is not None and _SENSITIVE_KEYS.search(key):
            return "[REDACTED]"
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, dict):
            return {
                str(item_key): self.mask_value(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [self.mask_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.mask_value(item) for item in value)
        return value
