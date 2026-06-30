# Realism hill-climb loop — state

**Objective:** raise the opus realism mean (24 merchant×operation analyses). Baseline v1 = 2.60, v2 (after
typography+content) = 2.62. Climb toward demo-real.
**Mode:** FULL-AUTO. Codex reviews the PLAN and the RESULT each round — the ONLY gates. No user pause: pick the
next backlog item and proceed. Deterministic ratchet + the two codex gates are the guardrails; user interrupts
only if it goes sideways. Report at each round's end (merge + score + next-item) without waiting for an OK.
**Isolation:** one fix per git worktree, file-disjoint, off `feat/synthesis-content-clean`; merge when verified.

## Parallelism: 3 LANES (keep all 3 busy; items within a lane run sequentially)
- **Lane A — synthesis/content** (`merchant_synthesis.py`): tax-flag → #2 reflow → item-line wiring → addr-guards.
- **Lane B — rendering** (`render_synthetic_receipts.py`, `receipt_renderer.py`, new render modules): #4 graphics → #5 texture.
- **Lane C — new modules** (NEW files only — no shared-file edits): item-line-grammar learner, scorecard tooling.
Cross-lane files never overlap → all merge clean. When a lane's agent merges, immediately start that lane's
next item (codex-review the plan first). Merge all landed lanes, then re-render + deterministic-score once.

## Ratchet metrics (cheap, deterministic — checked every round; opus is noisy so don't ratchet on it)
- reocr_gate pass-rate · propagation_f1 · glyph-height CV · price-decimal-x stddev · garbled-token count.
- Opus 24-agent realism re-score: run every ~3 rounds (expensive) — it sets DIRECTION, not the per-change gate.

## Done
- #1 grid typography (+ 3 codex bug-fixes) — glyph CV 0.097→0.000, prices right-aligned.
- #3 content reconciliation (totals cascade + item count) — 100% on totals-owning ops.
- Places-clean-all — garbled store addresses 9→5 (rest guard-protected).
- Pipeline assert → reject-not-crash.

## In flight
- ROUND 1 (content lane, step 1): tax-flag + name truncation. Worktree `~/Portfolio_taxflag`
  (branch `feat/item-tax-flag`, agent a07b10ab). Kills the `<A>` literal + missing-F/T tells. codex-approved scope.

## Backlog (ordered by the v2 re-score aggregate)
1. **#2 vertical reflow** — collapse grid wide word-gaps to single space + snap line-y to merchant pitch +
   reserve a grid row per totals line. Fixes the 63-mention spacing tell AND the totals overlap. (layout)
2. **item_line_template** — learner in `merchant_research/item_line_grammar.py` → intelligence block; then the
   `SALE 1@ … WAS: … each` sub-line in `_build_line_item_line`. Do AFTER #2 (geometry-changing). (content)
3. **#4 graphics** — real barcode (treepoem) + logo-subtitle fix + QR placement. (graphics)
4. **#5 texture** — augraphy thermal degradation pass, LAST. (paper-texture)
5. **addresses** — extend Places-clean to guard-protected 3-line cases. (content)

## Round log
- R1: tax-flag+truncation — building.
- R1 tax-flag MERGED (661d8bf): real F/T flags, <A> gone, 54 tests pass. Lane A -> #2 reflow next.
- R2 learner MERGED (46443aa): per-merchant item_line_template extractor (new files). Lane C -> scorecard tooling. Gap: target/home_depot flags need frequency review before wiring.
- R3 scorecard MERGED (e16762d): deterministic ratchet. BASELINE composite=0.643 on /tmp/realism_v2 (gap_p90 6.06 + voverlap 0.683 = the tells #2 reflow targets; garbled 0.004 good; verifier 0.981). Lane C -> learner flag-detection refinement.
- R4 graphics MERGED (5c4e074): real segno QR + python-barcode (Code128/UPC-A), fake bars+HRI-digits gone. Lane B -> logo-subtitle smear. BACKLOG+ (Lane A): reserve footer band for Costco/Vons barcodes.
- R5 grammar-refine MERGED (a9987c8): flag detection hardened (alphabet+column+freq-floor+name-density). target NF/T found, home_depot none-with-reason, sprouts noise dropped; 12 tests. Lane C backlog EXHAUSTED -> idle (rejoins for item-line wiring, which is Lane A after reflow).
- R6 reflow MERGED (ae67eab): compose-online-catalog vertical pitch (~32px, 0 overlap, integrity-gated) + respace gap floor tightened. 61 tests. CAVEATS->backlog: wide gaps from _build_template_filled_row char_w (Lane A), add_line_item vertical overlap (Lane A).
