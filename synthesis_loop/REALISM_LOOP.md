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
- R7 logo MERGED (e5c481c): logo bitmap is wordmark source-of-truth; Costco EWHOLESALE smear + Sprouts clipped sliver gone, Amazon no-regress. Lane B -> #5 texture.
- R8 item-line WIRING MERGED (f37e850, fixed): NOW marker + SALE sub-line via templates, full-cell gaps (codex VISUAL gate passed). Both major lanes in. -> BATCH measurement.

## R8 BATCH MEASUREMENT (cumulative, /tmp/realism_v3)
- Deterministic composite 0.643->0.640 (FLAT). vertical_overlap 0.683 UNCHANGED (reflow was compose-only; add/remove still collide). gap_p90 6.06->5.80 (marginal).
- Opus mean 2.62->2.58 (FLAT). #1 tell ALL 3 re-scores = LAYOUT (space39/align37/column36/right-align21/pitch17): pseudo-justified word spacing + no right-aligned price column.
- PLATEAU: 8 merges, no realism movement. Content/barcode/logo/flags landed but the DOMINANT layout tell is unfixed (we kept fixing it too narrowly: compose-only).
- RE-AIM: stop backlog cadence. Single highest-leverage PR = LAYOUT ROOT CAUSE: (1) _respace_visual_line non-uniform (preserve tight gaps, only fix overlaps), (2) right-align price column, (3) extend vertical pitch to add/remove ops. Codex diagnosing (pid 33922) with a rendered example.
- R9 tight-gap-contract MERGED (2ab208a): SALE-line floating gaps collapsed to single-space (deglue-only contract, render-time price->flag cursor). composite +0.011 on affected candidates, 96 tests. CAVEAT: dominant target/sprouts gaps are STRUCTURAL name->price (codex: correct), so aggregate move modest. -> full-matrix opus measure to decide if layout converges.

## R9 BATCH (/tmp/realism_v4) — PLATEAU BROKEN
- Opus mean 2.58 -> 2.71 (+0.13, beyond prior 0.04 wobble = real). Deterministic composite 0.640->0.644 (flat; weights gap_p90 only 0.10). The SALE-line gap fix moved the VISUAL realism. LAYOUT confirmed as the lever.
- v4 dominant remaining tells: right-aligned PRICE COLUMN (column 96/align 86/right-align 42) = #1; justified-spacing residue (justif 71); 'now'->'HOW' + reversed order 'F HOW $4.99' content bug (HOW 38).
- DECISION: CONTINUE layout backlog. Next = price-column right-edge anchor (codex #3: real amount-right-edge, not center _label_x_p50). Then vertical pitch on add/remove. Plus fix the now/HOW order bug.

## LOOP MECHANICS FIX (reinvoke gap)
- BUG: codex plan/visual reviews launched via 'nohup codex exec ... &' detach from the harness -> NO completion notification -> the loop idled until the 30-min heartbeat. (Agent + Workflow tasks ARE tracked and notify; only the nohup codex-exec step wasn't.)
- FIX: launch every codex review via the Bash tool's run_in_background:true (harness-tracked, re-invokes on exit), NOT nohup&. Same mechanism as the render-watchers. Closes the reinvoke gap; heartbeat stays as fallback only.
- R10 price-column MERGED (db1c420): synthetic LINE_TOTAL right-anchors to real margin (947->937). FINDING: metric flat (px_sd 30.9->30.9) because synthetic was ALREADY aligned + the high px_sd is REAL multi-column receipts (coupon cols), not a fake-tell. Scorecard px_sd is MISLEADING (measures real scaffold). Price-column was NOT the big lever. Next levers per v4: typography(serif-typewriter perception 95/65/68), now/HOW content bug(38), texture/graphics. INFLECTION: gains incremental; big win was the gap fix (2.58->2.71).
