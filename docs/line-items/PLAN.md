# Line-Item Extraction — Forward Plan (2026-08-03)

Source: three-agent retrospective (retro/), synthesized. The metric, the
tiers, and the kills below are the plan of record until superseded.

## The metric

**PROVEN receipts — a COUNT, in PROD**: receipts whose decoded items
reconcile to their printed baseline AND whose printed total matches an
independent bank-ledger amount. Baseline today: dev ≈ 250, **prod = 0**.
Counts, not rates: #1324 improved the match *rate* purely by reclassifying
16 broken baselines — nothing got more correct. Every work item below states
its expected movement of this count (or its eligibility denominator).

## NOW (one item per session; in this order)

1. **Prod summary-regen sweep** (822 receipts): verify #1327 merged, then send
   `{"entity_data":{image_id,receipt_id}}` per receipt to the chromadb-prod
   summary queue, throttled. Effect: tender fields 4→~815; band filter +
   printed-total fallback + three-figure baseline all recompute; prod mismatch
   848 → ~450. Then deploy the dev stack (warning #3). Verify recon
   distribution after.
2. **Bank-match prod** (LOCAL machine only — ledger sqlite + Apple PDFs live
   there): run the tender/bank backfill against `ReceiptsTable-d7ff76a`.
   PROVEN 0 → ~250. This step makes the metric measurable; nothing else counts
   until it runs.
3. **Zone-gap ITEMS boundary extension** — the #1 fix by receipts recovered
   (47) from failure_modes_report.md, de-risked by the merged band filter.
   Reconciliation-guided: absorb adjacent priced rows ONLY when |delta|
   strictly shrinks and status improves (same guard as extend_items_section).
   Both gates (golden floors, corpus sweep with per-receipt flip check).
   +30–47 PROVEN-eligible.
4. **De-dup the 19 split-receipt groups** (39 receipts, merge_receipts or
   retire-inferior-scan per the e1f519d5 precedent): they can never reconcile;
   −39 denominator, clears 11 phantom mismatches. Known pair still standing:
   879b0fa6 r2 / a63dbf2e r2 ($210.01, 2025-01-08).
5. **Sprouts totals regen residue**: #1321's fallback is merged; receipts
   whose summaries still carry grand_total 0/None just need recompute (mostly
   covered by item 1 — verify the 34-receipt list from tender_report clears).

## NEXT (this month)

- **Bank-proven golden loop FIRST**: auto-promote every PROVEN receipt into
  the golden set (33 → ~250) so no decoder change can silently undo the NOW
  work. Then self-labeling priors v3 harvested from bank-proven only.
- Swift parity repair: regenerate parity expectations from live Python in CI
  (red build on drift), re-port #1320's guards; only then wire the Mac worker
  as the line-item producer on new ingest (the on-device end-goal).
- Bank-gap work: 27 recoverable matcher misses; card-8712 + Amex exports
  (+~50 eligible); dev/prod parity (20 grand_total disagreements, 123 label
  drifts) via recompute, never row copy.
- CDN stragglers per stragglers report: 19 failed_original (crop-source
  fallback), 6 bad-geometry quads, 1 legacy-key.

## KILLED / PARKED (by name, with reason)

- **KILLED: further re-OCR-loop tuning** — targets digit fragmentation,
  measured at ~1 of 151 mismatches. The loop stays deployed; no more
  investment.
- **KILLED: any LLM pass over line items** — contradicts the minimal-LLM
  design goal; the deterministic path + arithmetic verification is the
  product.
- **KILLED: new tool surfaces** (UIs, MCP tools, CLIs) until zone-gap ships
  and the user has held ≥1 real review session in /dev/validation.
- **KILLED: frozen Swift parity fixture** — regenerate-in-CI or the port is
  decorative.
- PARKED: GeometricReader site figure (#1316 revert stands until user review),
  the 13 J-unknown mismatches, CORD re-runs, label-vocab cleanup as a
  standalone project, matcher fine-tuning beyond the 27 known misses.

## The user's single highest-leverage action

One 45-minute session in `/dev/validation` (branch codex/geometric-reader,
shim + next dev) over ~40 pre-queued receipts: the 6 bank-contradicted totals,
the 13 J-unknowns, ~20 PROVEN controls. review_log.jsonl is still empty; the
entire repair loop is designed around judgments only the user can make, and
the harness's premise is unfalsified until it hosts one real session.
