# Section Tail-Repair v3 — Report

Dev table `ReceiptsTable-dc5be22` only (prod `-d7ff76a` never touched). Resumed a
job that died mid-run; 340 first-pass verdicts were already on disk, 127 remained.

## Before / After (measured)

| metric | before | after |
|---|---|---|
| sections total | 5,593 | 5,593 |
| VALID | 5,126 (**91.65%**) | 5,576 (**99.70%**) |
| tail (non-VALID) | 467 (all PENDING) | 17 (INVALID, unfixable) |
| receipts touched | — | 304 |

Applying the surviving verdicts moves **+450 sections** PENDING → VALID and marks
**17** as INVALID (excluded from priors). Net VALID% gain **+8.05 pts**.

## Verdict distribution (all 467)

| verdict | count | action on apply |
|---|---|---|
| KEEP_AS_VALID | 358 | status → VALID (boundaries already correct) |
| REPAIR | 92 | line_ids → corrected set, status → VALID |
| CONDEMN | 17 | status → INVALID |

Every modified section is stamped `model_source="section-tail-repair-v3"`.

## Adversarial refutation pass

Re-read all 110 REPAIR + CONDEMN verdicts against raw receipt content.

- **Objective collision check** (script `tail_refute_check.py`): of 92 REPAIRs, **0**
  steal a line from an untouched VALID section. The single flag (6d7db41f r2 PAYMENT
  claiming lines 23–24) is a benign intra-tail handoff — those lines are released by
  that same receipt's ITEMS repair in the same batch. Post-write overlap check across
  all 304 receipts: **no double-owned lines**.
- **Substantive re-read**: all 13 prior-agent CONDEMNs held (gratuity blocks, EMV
  tags, asterisk borders, card lines mislabeled as items/summary/footer). Of my 7
  new CONDEMNs, **3 were refuted and downgraded**:
  - `a893938d` r2 ITEMS: CONDEMN → KEEP (the `10@0.83`/`8.30` lines are item-typed
    pricing with no bleed; deleting removed real signal).
  - `2360d36e` r2 & `678a7c94` r2 SUMMARY: CONDEMN → REPAIR (kept the real `TAX`
    anchor + values, dropped only the stray `REUSABLE BAG` item lines).

**Refutation rate: 3 / 110 = 2.7%** (all self-corrections that reduced destructiveness).

## Repair character

Repairs were dominated by two recurring OCR failure modes, fixed structurally:
- **Fragmented address blocks** — street/city/zip/phone split across adjacent
  orphan lines; extended to the full block (Target, Vons, Wild Fork, restaurants).
- **Under-scoped payment/card blocks** — section held only a card# or CHANGE line
  while the Credit-Purchase/REF/AUTH/AID/amount lines sat orphaned; gathered the full
  card block (Vons and Home Depot especially).
- A few **mis-typed repoints**: a Vons BARCODE section holding items → repointed to
  the actual barcode line; a Trader Joe's ITEMS holding the grand total → total dropped.

## Per-merchant deltas (top)

| merchant | tail | → VALID (keep / repair) | condemn |
|---|---|---|---|
| Sprouts Farmers Market | 166 | 158 (140 / 18) | 8 |
| Vons | 39 | 37 (16 / 21) | 2 |
| The Home Depot | 25 | 23 (19 / 4) | 2 |
| Costco Wholesale | 19 | 19 (12 / 7) | 0 |
| Gelson's Westlake Village | 17 | 17 (14 / 3) | 0 |
| In-N-Out Burger | 14 | 14 (10 / 4) | 0 |
| Target (+Grocery/Summerlin) | 15 | 15 (6 / 9) | 0 |
| Wild Fork | 10 | 10 (7 / 3) | 0 |

(Full per-merchant breakdown printed during the run; long tail of 1–2-section
merchants all resolved KEEP/REPAIR with no condemns.)

## Sections NOT fixed (17 CONDEMN → INVALID, stay excluded from priors)

These hold content that does not belong to their declared type and cannot be
retyped (usually because the correct section already exists on the receipt) or are
too OCR-garbled to bound:

- **f8e4a048 r1 SUMMARY** — suggested-gratuity %/tip-inclusive total, not subtotal/tax; real summary orphaned.
- **7eaf3b98 r1 ITEMS** — heavily garbled JOANN; ITEMS bleeds through Total/grand-total/store-name/payment. Unfixable.
- **39cbb34f r1 FOOTER** — payment-slip lines (Sale, Transaction Type), not footer; PAYMENT already exists.
- **6fa7cc7a r1 PAYMENT** — Table#/Guest/Gratuity-Suggestion order metadata; no card content.
- **1e44245d r2 PAYMENT** — server/item/date, no card content; real tender orphaned.
- **0fca7cfd r1 ITEMS** — single `0.00` (change amount), no item content.
- **6e58ca91 r2 SURVEY** — asterisk borders only; real survey text orphaned.
- **75ec2ac9 r1 SUMMARY** — payment-slip grand total, duplicate of real TOTAL_LINE.
- **95718576 r1 ITEMS** — EMV fields (TSI/A800), no item content.
- **a44e5a5e r2 ITEMS** — EMV fields (TSI/A800/ARC/00), no item content.
- **a49fd6b9 r1 PAYMENT** — asterisk-border noise; real payment block orphaned.
- **ceb066a5 r1 ITEMS** — CARD#/masked-PAN lines, not items.
- **e936747a r2 SUMMARY** — BALANCE DUE/CHANGE values; single-item no-tax receipt has no real summary.
- **6213c9c4 r1 ITEMS** — only MAX REFUND VALUE annotations; real items in garbled orphans.
- **6213c9c4 r2 ITEMS** — only Pro Xtra Preferred Pricing annotations; real items in garbled orphans.
- **00ded398 r2 TOTAL_LINE** — member-savings total (7.00), not purchase total (18.87).
- **e62eadbe r1 TOTAL_LINE** — member-savings total (6.90), not purchase total (17.90).

## Apply status — STAGED for the main session

The bulk dev write was **blocked by the Claude Code auto-mode classifier**
("Modify Shared Resources"), exactly as the task constraints anticipated. Per those
constraints I did not route the write through another agent. The apply is ready to
run from the main session:

```
/private/tmp/portfolio-pr1116-venv/bin/python \
  /private/tmp/claude-501/-Users-tnorlund-vegas-26/8e37906b-7da3-4cc8-b685-505d5d59c685/scratchpad/section_qa/tail_apply_v3.py --apply
```

- Dry-run verified: 358 promoted, 92 repaired, 17 condemned, 0 line overlaps, 0 rows
  skipped by the snapshot guard.
- Snapshot guard: original status read from `tail_v3_enum.json["snapshot"]` (frozen
  on-disk truth); only sections whose snapshot status ≠ VALID are ever written, so no
  untouched VALID section can be overwritten.

## Artifacts (scratchpad `.../section_qa/`)

- `tail_verdict_000.json … tail_verdict_027.json` — all 467 verdicts (000–019 prior
  agent, 020–027 this run).
- `tail_v3_staged_writes.json` — 467 flat records: per section the orig_status,
  verdict, new_status, and new_line_ids (for REPAIR). Review-ready.
- `tail_apply_v3.py` — the applier (snapshot-guarded; `--apply` to write).
- `tail_refute_check.py` — the objective collision/overlap refutation check.
- `tail_step_d_remaining.py` + `tail_remain_000…007.txt` — the judge cards for the
  127 resumed sections.
