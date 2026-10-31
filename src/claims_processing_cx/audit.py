"""Structured JSON audit events for each claim-processing step."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from claims_processing_cx.models import StrictModel
from claims_processing_cx.privacy import PIIMasker

LOGGER = logging.getLogger("claims_processing.audit")


def stable_hash(value: object) -> str:
    """Hash a JSON-compatible value without persisting its raw representation."""
    serialized = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AuditEvent(StrictModel):
    """Single sanitized pipeline step record."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    claim_id: str
    step: str
    latency_ms: float = Field(ge=0)
    input_hash: str
    output: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    token_usage: int = Field(default=0, ge=0)


class AuditTrail:
    """Writes one JSON file per claim while emitting structured log lines."""

    def __init__(self, directory: Path, masker: PIIMasker | None = None) -> None:
        self.directory = directory
        self.masker = masker or PIIMasker()
        self._events: dict[str, list[AuditEvent]] = {}

    def record(self, event: AuditEvent) -> None:
        """Append an event to memory and atomically replace its JSON trail."""
        event = event.model_copy(
            update={
                "output": self.masker.mask_value(event.output),
                "errors": self.masker.mask_value(event.errors),
            }
        )
        events = self._events.setdefault(event.claim_id, [])
        events.append(event)
        self.directory.mkdir(parents=True, exist_ok=True)
        filename = f"{stable_hash(event.claim_id)[:24]}.json"
        target = self.directory / filename
        temporary = target.with_suffix(".tmp")
        serialized = json.dumps(
            [item.model_dump(mode="json") for item in events],
            indent=2,
            sort_keys=True,
        )
        temporary.write_text(serialized + "\n", encoding="utf-8")
        temporary.replace(target)
        LOGGER.info(
            "%s",
            json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        )

    def events_for(self, claim_id: str) -> list[AuditEvent]:
        """Return the in-memory events for one claim."""
        return list(self._events.get(claim_id, []))
