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
- R11 marker-canonical MERGED (b020d34): GT HOW->now, SALES/SAVE->SALE 1@, order now $X.XX F. Found+fixed clean_token_text mis-repairing now/SALE (edit-dist-1 vocab corruption, non-deterministic). 47/47 now, 59/59 SALE, zero HOW. 91 tests. Residual: 8 VOID-SALE terminal words (clean_token_text follow-up).
## CHECKPOINT (v5) launching: re-render + scorecard + opus re-score -> climbing past 2.71 or plateau?
- R11 CHECKPOINT (v5): opus mean 2.71->2.71 PLATEAU (R10 price + R11 marker did NOT move it). Det composite 0.644 flat. Dominant tells now FOUNDATIONAL: typography (font reads serif/typewriter, never changed from VileR VGA: thermal119/monospace98/font75/dot-matrix55) + body word-spacing (space118/gap81) + HOW/SAVE still on add_line_item (cloned real rows). DECISION: STOP auto-grind, present fork to user.

## CODEX STRATEGIC VERDICT (v5 plateau, vision+xhigh on floor renders)
- **Open question -> C, not A.** The 2.0-floor ops fail on GRID COLLAPSE, not font. Target's fatal tell = summary rows fuse ("NV TAX 8.37500...TOTAL"); a better font won't fix it. Amazon floor = D (cloned grammar) + layout. So A+B raises the MEAN but leaves the worst renders visibly fake.
- **BIGGEST MISS (reframes the whole loop): the deterministic scorecard is BLIND.** layout_integrity scores SOURCE boxes and passes BOTH catastrophic renders (Target layout_integrity=1.0). It never scores actual RENDERED placements. => 11 PRs plateaued partly because we ratcheted a metric that can't see the real tell.
- **Sequencing:** PR1 = C grid discipline (overlap-aware row grouping receipt_grid.py:168 + placement planner: shared right amount lane, tax-flag lane, relative body gaps left of lanes; fold the SAFE part of B in; do NOT ship B standalone). PR2 = A font swap (Andale/PT Mono via glyph_ttf_fallback) + ink erosion (REUSE glyph_renderer.py:554 erosion, :1046 fade) -> must rerun PR1 lane scorecard (font swap changes glyph_advance=cell_w). PR3 = D cloned add-grammar (merchant_synthesis.py:1763 clone, :1320 helper, :325 canonicalizer too-narrow O-only).
- **Add a RENDERED-layout scorecard** (row-merge count, drawn-token overlap, amount-col variance, decimal-col variance, flag-lane collisions) — the measurement that would have caught these. Fold into PR1.
- Also: Amazon QR block missing (overlay only stamps if blank band fits, render_synthetic_receipts.py:826/883). Ink erosion separable from paper grain (hybrid already adds grain/vignette :523/:629; missing ink dropout).
- **"Not demo-good yet"** — QA thumbnails OK, not full-size side-by-side portfolio renders.
