# Responsible AI, privacy, and oversight

## Prototype scope

This repository uses synthetic records only. It is an interview/portfolio
prototype and is **not HIPAA-certified**, is not a covered-entity workflow, and
must not be populated with real patient or member data. The local mock LLM does
not send data to a provider.

## Minimum necessary and masking

The backend's `PIIMasker` redacts names and known synthetic identifiers, dates
of birth, SSNs, addresses, phone numbers, email addresses, ZIP codes, and NPIs
from free text and structured values. Clinical-documentation judgment receives
masked text. Audit input hashes are computed from masked inputs, and audit
outputs/errors are masked before persistence and structured logging. Raw
claim/appeal fixtures exist only in the local synthetic in-memory dataset.

Dialogflow flows collect identity fields deterministically and do not echo
them. Identity-gated CX tools verify member ID plus date of birth against the
claim owner and return only outcome, backend reason codes, and benefit amounts.
Playbooks are instructed to use only those returned facts. A real CX project
must separately validate platform conversation retention, model context,
redaction, logging, and region settings before any sensitive data is allowed;
this repository does not certify or implement Google-side PHI controls.

## Audit and human oversight

The local per-claim audit trail contains a stable input digest, step timings,
masked check output, errors, and token usage (zero for the stub). It is written
under `data/audit/`, which is ignored by Git. This is a prototype audit record,
not an immutable, access-controlled compliance ledger.

High-dollar claims, configured fraud/waste/abuse anomalies, low-confidence
documentation judgments, invalid schemas, and failed check validation are
`PENDED_FOR_REVIEW`. The reviewer queue is available at `GET /review-queue`.
The system does not autonomously resolve a human review or appeal.

## Fairness check and limits

The paired synthetic check holds claim and policy inputs constant while
changing only the linked member's name and ZIP code. It compares outcome,
reason codes, and payment amounts. It is a regression probe, not a fairness
certification: the fixtures are small and synthetic, the proxies are incomplete,
and the check does not evaluate protected classes, intersectionality,
clinical appropriateness, label quality, disparate impact, or production
populations.

## Real deployment work still required

A real healthcare deployment would additionally require, at minimum:

- an executed BAA with each applicable cloud/service provider and a documented
  HIPAA risk analysis;
- supported service configuration, approved regions, and verified data-use
  terms for Dialogflow CX/playbooks and any model service;
- encryption in transit and at rest, key management, least-privilege
  access controls, workload identity, and network isolation;
- retention/deletion policy, audit access restrictions, incident response,
  monitoring, backup/recovery, and validated identity proofing;
- clinical, legal, compliance, accessibility, and security review; and
- representative validation, human-review operations, and ongoing fairness and
  quality monitoring.

None of these requirements is represented as completed by this prototype.
