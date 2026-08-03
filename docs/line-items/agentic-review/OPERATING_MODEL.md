# Agentic Review Loop — Operating Model

This is the operating contract for the agentic-first review loop. It
distills `agentic_loop_design.md` (the 3-agent study's design) into the
form the scripts in this repo implement, and records the policy
constants the user decided on 2026-08-03. The study artifacts live next
to this file: `agentic_taxonomy.md` (what needs a human eye, per mode),
`pilot_findings.md` + `pilot_agent_reviews.jsonl` (the 14-receipt
vision pilot), and `review_workflow.md` (the superseded human-first
model, kept for the record).

## Policy constants (user-decided 2026-08-03; encoded, not re-askable)

- Fees/deposits/CRV printed as line entries ARE items (count toward
  sum).
- Bank match: category-gated tip band at food merchants (positive gap
  <= ~25%) counts as matched; tip recorded separately.
- Duplicate scans: RETIRE the inferior copy (backup first; e1f519d5
  recipe).
- PROVEN = exact-to-the-cent on both hops (<$0.005). "near" never
  counts.

Where these live in code:

| Constant | Code |
|---|---|
| PROVEN definition | `receipt_upload/receipt_upload/line_items/geometry.py::is_proven` |
| Tip band + category gate | `scripts/backfill_tender_bank.py` (`TIPPABLE`, tip-band match) |
| Fees/deposits are items | extractor keeps priced fee rows; only settlement/summary vocabulary is excluded (`geometry.py` band filter) |
| Duplicate retirement | `scripts/agentic_writer.py retire-duplicate` (backup-first, T2 sign-off required) |

## The loop: three passes

**P1 triage (read-only).** ~8 parallel read-only agents x ~15 receipts
cover every non-match receipt. Each agent uses
`scripts/agentic_triage_helpers.py` for the data digest and dry-run
extension simulation (<= 3 tool calls per receipt) and the FULL-RES
image for vision, then writes one dossier v2 per receipt under
`.dev-harness/dossiers/`. Prompt template and dossier schema:
`RUNBOOK_TRIAGE.md`. Vision answers "are the unclaimed rows
products?" — it never proposes a number.

**P2 adjudicate (files, never rows).** `scripts/agentic_adjudicate.py`
reads the dossiers and routes each into a trust tier, writing
`.dev-harness/verdicts/<pass-id>.jsonl` plus a grouped
`.dev-harness/verdicts/<pass-id>.digest.json` for the batch-approval
screen. It never touches DynamoDB.

**P3 writer (single-flight, guarded, dev table only).**
`scripts/agentic_writer.py` holds a lockfile
(`.dev-harness/writer.lock`), consumes T0 verdicts plus T1 groups the
human approved in `.dev-harness/approvals/<pass-id>.json`, and applies
ONE receipt at a time through the same arithmetic guard as the MCP tool
(`extend_items_section_impl`): preserve `validation_status`, stamp
`model_source` with `+agentic-vision-v1`, bump the summary
`timestamp_computed` so the stream regenerates line items, then re-read
and CONFIRM the predicted delta. Any divergence halts the whole run
with a report — never a retry. Refuses the prod table.

## Trust tiers

**T0 — auto-apply.** ALL of: contiguous extension, post-state `match`
(not merely improved), vision confirms every added line is a product,
and <= 5 per pass (overflow demotes to T1). Math decides, and a section
boundary is re-derivable, so the change is reversible.

**T1 — batch sign-off.** One digest row per (merchant x mode) group,
one Approve per group. Golden candidates live here and nowhere else:
promotion requires three independent concurring signals — arithmetic,
bank, vision — and still never auto-applies, because golden ratchets
CI floors permanently.

**T2 — per-receipt human.** `image_suspect`, destructive actions
(duplicate retirement, deletes), J-unknown mode, and any `flag` on a
green (match) row — a flag-on-green means the tolerance ladder is
producing false accepts and everything downstream is suspect.

**Abstain.** Everything else. An honest abstention beats a guess.

## Audit and freeze

10% of auto-verdicts (min 3/pass), random and blind: image and decoded
items shown, agent verdict hidden until the human commits. A single
disagreement freezes that tier/class: a marker file in
`.dev-harness/freeze/<tier-or-class>` demotes the frozen class to T2 in
every subsequent adjudication run until the marker is removed, and
auto-verdicts of that class since the last clean audit are re-queued.

## Failure containment

- Provenance: every verdict carries `verdict_by` (`agent:<pass-id>` |
  `human`) and `signals_concurring`; every applied repair records the
  pre-extension `line_ids` — the exact rollback key.
- Rollback: line items are derived; never patch them. Revert the
  section boundary and let the stream regenerate.
- Metric guard: PROVEN requires a bank anchor, so agent self-agreement
  cannot inflate it. An agent never authors both a repair and its
  confirmation (P1 and P3 are separate processes), and a receipt whose
  reconciliation came from an auto-applied repair does not count as
  PROVEN until it survives one clean audit cycle.

## The human surface (three screens)

Escalation queue (single-receipt view, T2), batch digest (one Approve
per T1 group), audit deck (blind). The human role is: policy (decided
above), golden sign-off, blind audits, and destructive-action
authority. Everything else is machine-adjudicable — the pilot showed
~95 of dev's 102 mismatches close once the reviewing agent has vision.
