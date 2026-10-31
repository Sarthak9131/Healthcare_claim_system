"""Mock-first language-model interface."""

from typing import Protocol


class LLMClient(Protocol):
    """Language tasks are abstracted so adjudication never depends on a live model."""

    async def explain_denial(self, reason_codes: list[str], benefit_context: str) -> str:
        """Explain a decision using only backend-provided reason codes and benefit context."""

    async def summarize_appeal(self, masked_appeal_text: str) -> str:
        """Summarize masked appeal text without making a coverage decision."""

    async def review_documentation(self, masked_documentation: str) -> tuple[bool, float, str]:
        """Return a judgment signal, confidence, and concise rationale."""


class StubLLMClient:
    """Deterministic local implementation requiring no API key or network."""

    async def explain_denial(self, reason_codes: list[str], benefit_context: str) -> str:
        """Return a cautious explanation grounded in supplied codes."""
        codes = ", ".join(reason_codes) if reason_codes else "no specific reason code"
        return f"The claim needs attention because of {codes}. {benefit_context}".strip()

    async def summarize_appeal(self, masked_appeal_text: str) -> str:
        """Return a bounded summary of synthetic or masked appeal text."""
        return f"Appeal summary: {masked_appeal_text[:500]}"

    async def review_documentation(self, masked_documentation: str) -> tuple[bool, float, str]:
        """Provide a conservative deterministic documentation signal."""
        present = bool(masked_documentation.strip())
        return present, 0.5 if present else 0.0, "Stub review; human review remains authoritative."
