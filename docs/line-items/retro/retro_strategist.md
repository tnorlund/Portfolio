# Retro — Strategist: the way forward

## (a) The one metric: PROVEN RECEIPTS — a count, in prod
A receipt is PROVEN when its item sum reconciles to a printed baseline **and** its printed grand total equals an independent ledger amount. Count, not rate. Prod, not dev.
Why not "corpus match rate": rates are gameable by the denominator — #1324 moved 16 mismatches to `no-baseline` and the rate held at 452 while nothing became more correct; a count can only rise by fixing a receipt. Arithmetic alone certifies the 19 split-receipt groups that each carry a full total and partial items (self-consistent, wrong). Bank alone is unusable as a negative (tender §8: non-matches are dominated by cash / no-ledger / out-of-window). The conjunction is exactly the user's stated goal and is falsifiable per receipt.
Today: dev ≈ 250 (452 match ∩ 349 bank-matched), **prod = 0** — prod holds 0 `bank_amount` rows and 848 mismatches. Report it as "N of 822" at the top of every session.

## (b) NOW — this week, 5 items
1. Merge #1327 (green), then fire the 822-receipt **prod summary-regen sweep**. Prod recomputes tender/last4 from its own words (4 → ~815) and re-runs the merged band filter + Sprouts fallback + three-figure baseline. Prod mismatch 848 → ~450. Follow with `pulumi up --stack dev` (memory rule).
2. **Run the bank matcher against prod** and write `ledger`/`bank_amount`/`bank_match_confidence` from the local SQLite ledger (`scripts/backfill_bank_match.py --table prod`). PROVEN 0 → ~250. First among equals: without it the metric isn't measurable at all.
3. **Zone-gap ITEMS boundary extension** (47 receipts) — largest un-shipped decoder fix, and the overshoot risk that graded it "medium" was retired when the band filter merged in #1320. **+30–47 PROVEN.**
4. **De-dup the 19 split-receipt groups** (39 receipts) with `merge_receipts`. They can never be PROVEN and permanently poison per-merchant rates; clears 11 phantom mismatches. Denominator −39.
5. **Fix Sprouts GRAND_TOTAL extraction** (`0.0`/`None` on 24 of the 111 bank misses; Sprouts is 188 receipts / 48 mismatches). +~24 eligible, **+~15 PROVEN** — best return per hour on the board.

## (c) NEXT — this month
- **Bank-proven golden loop first.** Auto-promote every PROVEN receipt into the golden set (33 → ~250). This is the flywheel: it turns the metric into a regression suite so no later decoder change can silently undo the NOW work. Do it before any further decoder tuning.
- Work the 111-receipt bank gap: 27 recoverable matcher misses (→81.7% coverage), 60 receipt-side.
- Pull the card-8712 and Amex-6081 exports: +~50 eligible receipts, ~$3.5k of spend now invisible.
- dev/prod parity: 20 `grand_total` disagreements, 123 label drifts — via the label-sync path, never a row copy.
- **Make the Mac worker the producer of line items on new ingest.** Swift decoder + section assigner are already at 33/33 parity; this is the user's on-device end-goal and it is roughly one wiring change away.
- CDN stragglers: crop-source fallback for the 19 `failed_original`; recompute the 6 bad quads.

## (d) LATER / NEVER — named kills
- **NEVER invest further in the re-OCR loop as a quality lever.** It targets digit-fragmentation: 1 of 151 mismatches. It is built and running — leave it, stop tuning it.
- **NEVER add a third parser or an LLM pass over line items.** Directly contradicts the minimal-LLM end-goal; `reconstructor.py` + the block decoder already exist.
- **NEVER run label-vocab cleanup as a project.** Clean only what blocks a specific PROVEN receipt.
- PARK: the 13 `J-unknown` mismatches and the 15 shortfalls with no priced text in OCR at all (these need re-OCR, not code). Revisit only if PROVEN plateaus above 700.
- PARK: GeometricReader / #1316 viz — ships only behind a user review session, never Claude-initiated. PARK: legacy flat CDN key migration (9 receipts, cosmetic) and CORD external validation (someone else's corpus).

## (e) Working model — how to not produce another unreviewable session
1. **One session = one metric delta, declared in the first message as a number.** This session took six sequential goals; that is the whole root cause of the sprawl. A new goal is a new session, not a pivot.
2. **Never mix exploratory analysis with prod writes.** Analysis sessions emit reports and PRs only; prod writes get their own short session, plan written first, destructive step provably last. Four of the eight logged incidents were prod writes inside an exploratory session.
3. **Spawn agents only for bounded, read-only corpus analysis.** The three that succeeded (failure-modes, tender, divergence) are this session's best artifacts. Never spawn one for a prod write, for work that will hit a permission prompt mid-sequence, or for anything whose success you can't state as a number up front — every stalled agent was an open-ended write task.
4. **≤3 open PRs per session; zero frontend merges without the user's eyes** (the #1317 revert already established this).
5. Split of labor: user merges, approves prod writes, reviews anything visual, and supplies local/private data. Claude does analysis, PRs, dry-runs, and scripted execution of an already-approved plan.

## (f) The single highest-leverage USER action
**One 45-minute review session in /dev/validation on ~40 pre-queued receipts** — the 6 bank-contradicted totals, the 13 J-unknowns, and 20 PROVEN controls. It is the only input Claude cannot manufacture; the harness everything was built around has never once been exercised, so its central premise is currently unfalsified; and its output (`review_log.jsonl`) is the seed corpus for the bank-proven golden loop in (c). If the loop doesn't work, this week is a far cheaper time to find out than after five more PRs are stacked on top of it.
