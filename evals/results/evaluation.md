# Synthetic claims evaluation

- Generated: `2026-10-31T07:14:51.437164+00:00`
- Dataset: `128` claims, seed `20261031`
- Mode: local deterministic rules and stub LLM; no GCP calls

## Adjudication

- Decision accuracy: `1.0000` (128/128)
- Exact reason-code accuracy: `1.0000` (128/128)
- Exact consistency over 3 runs: `1.0000`

| Outcome | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| PAID | 32 | 1.0000 | 1.0000 | 1.0000 |
| PARTIALLY_PAID | 32 | 1.0000 | 1.0000 | 1.0000 |
| DENIED | 32 | 1.0000 | 1.0000 | 1.0000 |
| PENDED_FOR_REVIEW | 32 | 1.0000 | 1.0000 | 1.0000 |

## Confusion matrix (rows expected, columns predicted)

| Expected \ Predicted | PAID | PARTIALLY_PAID | DENIED | PENDED_FOR_REVIEW |
|---|---:|---:|---:|---:|
| PAID | 32 | 0 | 0 | 0 |
| PARTIALLY_PAID | 0 | 32 | 0 | 0 |
| DENIED | 0 | 0 | 32 | 0 |
| PENDED_FOR_REVIEW | 0 | 0 | 0 | 32 |

## Latency by phase (measured milliseconds)

| Phase | Samples | Mean | Median | P95 |
|---|---:|---:|---:|---:|
| intake | 384 | 0.120 | 0.110 | 0.184 |
| parallel_checks | 366 | 34.214 | 32.652 | 44.628 |
| adjudicator | 366 | 0.108 | 0.090 | 0.155 |
| compliance_review | 366 | 0.077 | 0.068 | 0.120 |

## Rules-only comparison

- Mock-assisted accuracy: `1.0000`
- Rules-only accuracy: `0.9375`
- Cases changed by the mock judgment path: 8
- Mock-only correct cases: 8
- Rules-only correct cases: 0
- Interpretation: This comparison measures only the local stub's low-confidence review routing; it is not evidence that a production LLM improves adjudication.

## Token usage

- Mock-assisted tokens: `0`
- Rules-only tokens: `0`
- Cost estimate: not applicable; no paid model was invoked.

## Conversational simulator and fairness

- Local dialogue intent-routing accuracy: `1.0000`
- Escalation correctness: `1.0000`
- No-PII-leakage rate: `1.0000`
- Denial grounding: `1.0000` over 1 applicable dialogue(s)
- Paired fairness disagreement: `0.0000` (0/30)

## Failure-case analysis

No labeled claim mismatches were observed in this run.

> The CX conversation scores come from the deterministic local simulator, not a live Dialogflow CX model or deployed agent. All claims are synthetic. The stub is not evidence of production LLM performance.
