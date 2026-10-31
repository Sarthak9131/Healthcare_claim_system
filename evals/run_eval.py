"""Run adjudication, repeatability, fairness, and local CX dialogue evaluations."""

import argparse
import asyncio
import json
import math
import statistics
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claims_processing_cx.audit import AuditTrail
from claims_processing_cx.fairness import compare_demographic_pair
from claims_processing_cx.llm import StubLLMClient
from claims_processing_cx.models import (
    Claim,
    Decision,
    DecisionStatus,
    SyntheticDataset,
)
from claims_processing_cx.pipeline import ClaimsPipeline
from claims_processing_cx.storage import InMemoryStorage
from evals.conversation_eval import run_conversation_evaluation
from evals.generate_dataset import (
    DEFAULT_OUTPUT,
    EvaluationDataset,
)

ROOT = Path(__file__).parents[1]
DEFAULT_RESULTS = ROOT / "evals" / "results"
_STATUSES = [status.value for status in DecisionStatus]
_PHASES = ("intake", "parallel_checks", "adjudicator", "compliance_review")


async def run_pipeline(
    evaluation_data: EvaluationDataset,
    *,
    rules_only: bool,
) -> tuple[dict[str, Decision], dict[str, list[float]], int]:
    """Run all cases once with a mock judgment or an explicit rules-only baseline."""
    data = _synthetic_dataset(evaluation_data)
    storage = InMemoryStorage(data)
    with tempfile.TemporaryDirectory(prefix="claims-eval-") as directory:
        audit = AuditTrail(Path(directory) / "audit")
        pipeline = ClaimsPipeline(storage, StubLLMClient(), audit)
        if rules_only:

            async def no_language_judgment(_: Claim) -> dict[str, object]:
                return {
                    "check_name": "documentation_consistency",
                    "passed": True,
                    "details": "Rules-only baseline does not use a language judgment.",
                    "confidence": "1.0",
                    "metadata": {"mode": "rules-only"},
                }

            pipeline.checks.documentation_consistency = no_language_judgment

        decisions: dict[str, Decision] = {}
        latencies = {phase: [] for phase in _PHASES}
        tokens = 0
        for case in evaluation_data.cases:
            decision = await pipeline.adjudicate(case.claim.model_dump(mode="json"))
            decisions[case.claim.claim_id] = decision
            for event in audit.events_for(case.claim.claim_id):
                if event.step in latencies:
                    latencies[event.step].append(event.latency_ms)
                tokens += event.token_usage
        return decisions, latencies, tokens


async def evaluate(evaluation_data: EvaluationDataset) -> dict[str, Any]:
    """Compute every metric from the labeled dataset and actual pipeline results."""
    repeated_runs: list[dict[str, Decision]] = []
    phase_latencies: dict[str, list[float]] = {phase: [] for phase in _PHASES}
    token_usage = 0
    for _ in range(3):
        run_decisions, run_latencies, run_tokens = await run_pipeline(
            evaluation_data,
            rules_only=False,
        )
        repeated_runs.append(run_decisions)
        token_usage += run_tokens
        for phase in _PHASES:
            phase_latencies[phase].extend(run_latencies[phase])

    baseline, _, baseline_tokens = await run_pipeline(evaluation_data, rules_only=True)
    primary = repeated_runs[0]
    metrics = _decision_metrics(evaluation_data, primary)
    metrics["repeatability"] = _repeatability(evaluation_data, repeated_runs)
    metrics["phase_latency_ms"] = {
        phase: _summarize(latencies) for phase, latencies in phase_latencies.items()
    }
    metrics["token_usage"] = {
        "mock_assisted_tokens": token_usage,
        "rules_only_tokens": baseline_tokens,
        "cost_estimate_usd": None,
        "note": "The local stub uses no paid model; no token price is assumed.",
    }
    metrics["rules_only_comparison"] = _baseline_comparison(
        evaluation_data,
        primary,
        baseline,
    )
    metrics["fairness"] = await _fairness_metrics(evaluation_data)
    metrics["conversation"] = await run_conversation_evaluation(
        evaluation_data,
        ROOT / "cx_agent" / "conversation-cases.json",
    )
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_mode": "local deterministic rules and stub LLM; no GCP calls",
        "dataset": {
            "path": DEFAULT_OUTPUT.relative_to(ROOT).as_posix(),
            "seed": evaluation_data.seed,
            "claim_count": len(evaluation_data.cases),
        },
        "metrics": metrics,
        "failure_cases": _failure_cases(evaluation_data, primary),
    }


def _decision_metrics(
    evaluation_data: EvaluationDataset,
    decisions: dict[str, Decision],
) -> dict[str, Any]:
    """Calculate accuracy, class metrics, confusion matrix, and reason-code accuracy."""
    cases = evaluation_data.cases
    confusion = {expected: {actual: 0 for actual in _STATUSES} for expected in _STATUSES}
    true_positives = Counter[str]()
    predicted_count = Counter[str]()
    expected_count = Counter[str]()
    correct = 0
    reason_exact = 0
    for case in cases:
        actual = decisions[case.claim.claim_id]
        expected_status = case.expected_status.value
        actual_status = actual.status.value
        confusion[expected_status][actual_status] += 1
        expected_count[expected_status] += 1
        predicted_count[actual_status] += 1
        if expected_status == actual_status:
            correct += 1
            true_positives[actual_status] += 1
        if set(case.expected_reason_codes) == {item.code for item in actual.reason_codes}:
            reason_exact += 1
    class_metrics: dict[str, Any] = {}
    for status in _STATUSES:
        precision = (
            true_positives[status] / predicted_count[status]
            if predicted_count[status]
            else 0.0
        )
        recall = true_positives[status] / expected_count[status] if expected_count[status] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        class_metrics[status] = {
            "support": expected_count[status],
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return {
        "decision_accuracy": correct / len(cases) if cases else 0.0,
        "correct_decisions": correct,
        "case_count": len(cases),
        "per_class": class_metrics,
        "confusion_matrix": {
            "labels": _STATUSES,
            "rows_expected_columns_predicted": confusion,
        },
        "reason_code_exact_accuracy": reason_exact / len(cases) if cases else 0.0,
        "reason_code_exact_matches": reason_exact,
    }


def _repeatability(
    evaluation_data: EvaluationDataset,
    runs: list[dict[str, Decision]],
) -> dict[str, Any]:
    """Require status, code, and benefit amounts to agree across the three runs."""
    consistent = 0
    for case in evaluation_data.cases:
        signatures = [_decision_signature(run[case.claim.claim_id]) for run in runs]
        consistent += len(set(signatures)) == 1
    count = len(evaluation_data.cases)
    return {
        "run_count": len(runs),
        "consistent_claims": consistent,
        "claim_count": count,
        "exact_consistency_rate": consistent / count if count else 0.0,
        "signature": "status, sorted reason codes, allowed, paid, member responsibility",
    }


def _decision_signature(decision: Decision) -> tuple[object, ...]:
    """Return all user-visible adjudication outputs used for repeatability."""
    return (
        decision.status.value,
        tuple(sorted(item.code for item in decision.reason_codes)),
        str(decision.allowed_amount),
        str(decision.paid_amount),
        str(decision.member_responsibility),
    )


def _baseline_comparison(
    evaluation_data: EvaluationDataset,
    assisted: dict[str, Decision],
    baseline: dict[str, Decision],
) -> dict[str, Any]:
    """Compare mock judgment routing with an otherwise identical rules-only path."""
    assisted_correct = 0
    baseline_correct = 0
    assisted_only = 0
    baseline_only = 0
    affected: list[dict[str, str]] = []
    for case in evaluation_data.cases:
        assisted_decision = assisted[case.claim.claim_id]
        baseline_decision = baseline[case.claim.claim_id]
        assisted_ok = assisted_decision.status == case.expected_status
        baseline_ok = baseline_decision.status == case.expected_status
        assisted_correct += assisted_ok
        baseline_correct += baseline_ok
        assisted_only += assisted_ok and not baseline_ok
        baseline_only += baseline_ok and not assisted_ok
        if assisted_decision.status != baseline_decision.status:
            affected.append(
                {
                    "claim_id": case.claim.claim_id,
                    "scenario": case.scenario,
                    "mock_assisted_status": assisted_decision.status.value,
                    "rules_only_status": baseline_decision.status.value,
                }
            )
    total = len(evaluation_data.cases)
    return {
        "baseline_name": "same deterministic rules with documentation judgment bypassed",
        "mock_assisted_accuracy": assisted_correct / total if total else 0.0,
        "rules_only_accuracy": baseline_correct / total if total else 0.0,
        "mock_only_correct_cases": assisted_only,
        "rules_only_correct_cases": baseline_only,
        "decision_changes": affected,
        "interpretation": (
            "This comparison measures only the local stub's low-confidence review routing; "
            "it is not evidence that a production LLM improves adjudication."
        ),
    }


async def _fairness_metrics(evaluation_data: EvaluationDataset) -> dict[str, Any]:
    """Run a deterministic three-pair-per-scenario synthetic proxy sample."""
    data = _synthetic_dataset(evaluation_data)
    compared = []
    skipped = 0
    sampled_by_scenario: Counter[str] = Counter()
    eligible_cases = 0
    for case in evaluation_data.cases:
        if case.claim.member_id is None:
            skipped += 1
            continue
        eligible_cases += 1
        if sampled_by_scenario[case.scenario] >= 3:
            continue
        sampled_by_scenario[case.scenario] += 1
        result = await compare_demographic_pair(
            case.claim,
            data,
            alternate_name=f"Alternate Synthetic {case.claim.claim_id[-4:]}",
            alternate_zip_code="99999",
        )
        compared.append(result)
    disagreements = sum(result.decision_disagreement for result in compared)
    return {
        "paired_cases": len(compared),
        "eligible_cases": eligible_cases,
        "not_sampled": eligible_cases - len(compared),
        "skipped_missing_member": skipped,
        "decision_disagreements": disagreements,
        "decision_disagreement_rate": disagreements / len(compared) if compared else None,
        "changed_proxies": ["name", "ZIP code"],
        "limitations": (
            "Synthetic paired regression probe only; not a fairness certification "
            "or population analysis."
        ),
    }


def _failure_cases(
    evaluation_data: EvaluationDataset,
    decisions: dict[str, Decision],
) -> list[dict[str, Any]]:
    """Explain mismatched labels with a conservative likely-cause hypothesis."""
    failures: list[dict[str, Any]] = []
    for case in evaluation_data.cases:
        actual = decisions[case.claim.claim_id]
        actual_codes = [item.code for item in actual.reason_codes]
        if actual.status == case.expected_status and set(actual_codes) == set(
            case.expected_reason_codes
        ):
            continue
        likely_cause = (
            "Outcome status differs from the independent synthetic label."
            if actual.status != case.expected_status
            else "Reason-code set differs although the outcome status matches."
        )
        if case.scenario == "low_confidence_documentation":
            likely_cause += (
                " The documentation judgment confidence threshold may have routed "
                "it to review."
            )
        failures.append(
            {
                "claim_id": case.claim.claim_id,
                "scenario": case.scenario,
                "expected_status": case.expected_status.value,
                "actual_status": actual.status.value,
                "expected_reason_codes": case.expected_reason_codes,
                "actual_reason_codes": actual_codes,
                "likely_cause": likely_cause,
            }
        )
    return failures


def _synthetic_dataset(evaluation_data: EvaluationDataset) -> SyntheticDataset:
    """Build the in-memory service dataset without expected-label leakage."""
    cases = evaluation_data.cases
    return SyntheticDataset(
        seed=evaluation_data.seed,
        members=[case.member for case in cases],
        providers=list({case.provider.provider_id: case.provider for case in cases}.values()),
        policies=[case.policy for case in cases],
        claims=[case.claim for case in cases],
    )


def _summarize(values: list[float]) -> dict[str, float | int | None]:
    """Summarize measured phase latency without inventing missing values."""
    if not values:
        return {"samples": 0, "mean": None, "median": None, "p95": None}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "samples": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p95": ordered[p95_index],
    }


def render_markdown(results: dict[str, Any]) -> str:
    """Render a concise, reproducible evaluation summary from saved results."""
    metrics = results["metrics"]
    lines = [
        "# Synthetic claims evaluation",
        "",
        f"- Generated: `{results['generated_at_utc']}`",
        f"- Dataset: `{results['dataset']['claim_count']}` claims, "
        f"seed `{results['dataset']['seed']}`",
        f"- Mode: {results['evaluation_mode']}",
        "",
        "## Adjudication",
        "",
        f"- Decision accuracy: `{metrics['decision_accuracy']:.4f}` "
        f"({metrics['correct_decisions']}/{metrics['case_count']})",
        f"- Exact reason-code accuracy: `{metrics['reason_code_exact_accuracy']:.4f}` "
        f"({metrics['reason_code_exact_matches']}/{metrics['case_count']})",
        f"- Exact consistency over {metrics['repeatability']['run_count']} runs: "
        f"`{metrics['repeatability']['exact_consistency_rate']:.4f}`",
        "",
        "| Outcome | Support | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|",
    ]
    for status, values in metrics["per_class"].items():
        lines.append(
            f"| {status} | {values['support']} | {values['precision']:.4f} | "
            f"{values['recall']:.4f} | {values['f1']:.4f} |"
        )
    lines.extend(["", "## Confusion matrix (rows expected, columns predicted)", ""])
    labels = metrics["confusion_matrix"]["labels"]
    matrix = metrics["confusion_matrix"]["rows_expected_columns_predicted"]
    lines.append("| Expected \\ Predicted | " + " | ".join(labels) + " |")
    lines.append("|---|" + "---:|" * len(labels))
    for expected in labels:
        lines.append(
            f"| {expected} | "
            + " | ".join(str(matrix[expected][actual]) for actual in labels)
            + " |"
        )
    lines.extend(
        [
            "",
            "## Latency by phase (measured milliseconds)",
            "",
            "| Phase | Samples | Mean | Median | P95 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for phase, summary in metrics["phase_latency_ms"].items():
        lines.append(
            f"| {phase} | {summary['samples']} | {_fmt(summary['mean'])} | "
            f"{_fmt(summary['median'])} | {_fmt(summary['p95'])} |"
        )
    lines.extend(
        [
            "",
            "## Rules-only comparison",
            "",
            f"- Mock-assisted accuracy: "
            f"`{metrics['rules_only_comparison']['mock_assisted_accuracy']:.4f}`",
            f"- Rules-only accuracy: "
            f"`{metrics['rules_only_comparison']['rules_only_accuracy']:.4f}`",
            f"- Cases changed by the mock judgment path: "
            f"{len(metrics['rules_only_comparison']['decision_changes'])}",
            f"- Mock-only correct cases: "
            f"{metrics['rules_only_comparison']['mock_only_correct_cases']}",
            f"- Rules-only correct cases: "
            f"{metrics['rules_only_comparison']['rules_only_correct_cases']}",
            f"- Interpretation: {metrics['rules_only_comparison']['interpretation']}",
            "",
            "## Token usage",
            "",
            f"- Mock-assisted tokens: `{metrics['token_usage']['mock_assisted_tokens']}`",
            f"- Rules-only tokens: `{metrics['token_usage']['rules_only_tokens']}`",
            "- Cost estimate: not applicable; no paid model was invoked.",
            "",
            "## Conversational simulator and fairness",
            "",
            f"- Local dialogue intent-routing accuracy: "
            f"`{metrics['conversation']['intent_routing_accuracy']:.4f}`",
            f"- Escalation correctness: `{metrics['conversation']['escalation_correctness']:.4f}`",
            f"- No-PII-leakage rate: `{metrics['conversation']['no_phi_leakage_rate']:.4f}`",
            f"- Denial grounding: "
            f"`{metrics['conversation']['denial_explanation_groundedness']:.4f}` "
            f"over {metrics['conversation']['groundedness_dialogues']} applicable dialogue(s)",
            f"- Paired fairness disagreement: "
            f"`{metrics['fairness']['decision_disagreement_rate']:.4f}` "
            f"({metrics['fairness']['decision_disagreements']}/"
            f"{metrics['fairness']['paired_cases']})",
            "",
            "## Failure-case analysis",
            "",
        ]
    )
    if results["failure_cases"]:
        lines.extend(
            [
                "| Claim | Scenario | Expected | Actual | Expected reasons | "
                "Actual reasons | Likely cause |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for failure in results["failure_cases"]:
            lines.append(
                f"| {failure['claim_id']} | {failure['scenario']} | "
                f"{failure['expected_status']} | {failure['actual_status']} | "
                f"{', '.join(failure['expected_reason_codes']) or 'none'} | "
                f"{', '.join(failure['actual_reason_codes']) or 'none'} | "
                f"{failure['likely_cause']} |"
            )
    else:
        lines.append("No labeled claim mismatches were observed in this run.")
    lines.extend(
        [
            "",
            "> The CX conversation scores come from the deterministic local simulator, "
            "not a live Dialogflow CX model or deployed agent. All claims are synthetic. "
            "The stub is not evidence of production LLM performance.",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    """Format measured values while preserving empty phases."""
    return f"{value:.3f}" if value is not None else "n/a"


async def run_and_save(
    dataset_path: Path = DEFAULT_OUTPUT,
    results_directory: Path = DEFAULT_RESULTS,
) -> dict[str, Any]:
    """Load/generate the labeled set, evaluate it, and save JSON plus Markdown."""
    if not dataset_path.exists():
        from evals.generate_dataset import write_dataset

        write_dataset(dataset_path)
    evaluation_data = EvaluationDataset.model_validate_json(
        dataset_path.read_text(encoding="utf-8")
    )
    results = await evaluate(evaluation_data)
    results_directory.mkdir(parents=True, exist_ok=True)
    (results_directory / "evaluation.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (results_directory / "evaluation.md").write_text(
        render_markdown(results),
        encoding="utf-8",
    )
    return results


def main() -> None:
    """CLI entry point; all reported metrics are computed by this process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    arguments = parser.parse_args()
    results = asyncio.run(run_and_save(arguments.dataset, arguments.results))
    metrics = results["metrics"]
    print(
        f"Evaluated {results['dataset']['claim_count']} claims; "
        f"decision accuracy={metrics['decision_accuracy']:.4f}; "
        f"reason-code accuracy={metrics['reason_code_exact_accuracy']:.4f}; "
        f"repeatability={metrics['repeatability']['exact_consistency_rate']:.4f}"
    )
    print(f"Results saved under {arguments.results}")


if __name__ == "__main__":
    main()
