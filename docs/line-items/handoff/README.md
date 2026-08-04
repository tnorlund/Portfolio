# Handoff — smart re-OCR + line-item validation (2026-08-04)

Start here. Companions: `STATE_2026-08-04.md` (full census, verified numbers),
`SWIFT_AND_GEOMETRY.md` (the unfinished Swift work + the tilt root cause).
Background: `../STATE_OF_THE_SYSTEM.md`, `../PLAN.md`, `../agentic-review/`.

## Where things actually stand

The deterministic line-item pipeline is live on both stacks and improving:
dev **PROVEN 281/821**, prod **298/822** (PROVEN = items reconcile to the
printed baseline AND the printed total matches a bank ledger amount, cent-exact).
The agentic review loop (triage → adjudicate → guarded writer) ran one full
cycle: 187 receipts triaged with vision, 7 repairs applied and confirmed, the
first 2 agent-originated golden promotions merged (golden set 35).

**The re-OCR intelligence shipped without its muscle.** #1350 merged the
Python half — strategy ladder (`plain|invert|deskew|upscale2x`), OCRJob
contract fields, outcome harvest, mechanism-aware triggers. The Swift half
(image preprocessing before Vision) was written but **never committed**. So
every retry today is still a plain re-read, and `reocr_strategy` is set on
0 of 634 REGIONAL_REOCR jobs across both tables.

## The four things that are actually broken

1. **No machine drains the OCR queues.** The MacBook's LaunchAgent plist
   exists but was never `launchctl load`ed (no job, no log ever written); the
   mini has nothing installed. **74 re-OCR jobs are queued with no consumer**
   (10 dev + 64 prod — prod's queue has never been drained). Jobs that did
   complete were drained by hand.
2. **The Swift preprocessing is uncommitted, not unwritten.** ~90% done, 4
   files + 490 lines of tests, intact in worktree
   `/Users/tnorlund/Portfolio/.claude/worktrees/agent-a8c5ed403a0d5bcd6`
   (branch `feat/reocr-strategies`, 0 commits ahead — **do not delete**), with
   a warm 2 GB `.build`. Needs: commit → build → test → PR.
3. **Tilt was never measured — a hardcoded zero, not a missing feature.**
   `VisionOCREngine.swift` hardcodes `angleDegrees: 0.0` (L442/L460/L478) and
   synthesizes word corners from an axis-aligned `CGRect` (L264-271); Vision's
   real quad corners are used only for barcodes. Every receipt quad in the
   corpus is therefore axis-aligned, which is why tilted receipts (e.g. dev
   `492f9ae1…`) cannot be fixed by re-OCR or resegmentation. Fix is ~10 lines
   — but it changes first-pass geometry for all future uploads (contract
   fixture + golden churn to plan for).
4. **Dev has eroded relative to prod.** Prod carries 64 more summaries with a
   `grand_total` and 17 more PROVEN; dev holds 46 more receipts at `none`
   recon. Dev is where the agentic writer ran. **Explain this before any
   dev→prod promotion** — do not assume dev is the newer truth.

## Suggested order

1. Get both workers running (see `../agentic-review/RUNNERS.md` and
   `scripts/update_ocr_workers.sh` — run it after every `receipt_ocr_swift`
   merge; the launchd agents point at compiled binaries that never
   auto-update). Then drain dev's 10 and prod's 64 queued jobs.
2. Land the Swift preprocessing PR (build it on the **mini** — it has Xcode;
   this MacBook is CommandLineTools-only, so tests use swift-testing and cold
   builds are slow). Rebuild both workers afterwards.
3. Re-trigger the mismatch population so retries use real strategies, then run
   `scripts/harvest_reocr_outcomes.py` to start the success ledger.
4. Investigate the dev erosion (item 4) before promoting anything.
5. Fix the tilt zero (item 3) as its own PR with fixture regeneration.

## Known-stale, don't be fooled

- The 207 dossiers in `.dev-harness/` predate #1349's tender fix — regenerate
  before reusing; `review_log.jsonl` is empty (visual review happened via a
  generated HTML report, not the harness UI).
- The review harness itself is **unmerged**: branch `codex/geometric-reader`,
  +7 commits / ~10k lines, no PR. It is local-only tooling by design.
- Swift line-item decoder parity is frozen pre-#1320/#1321/#1349 — the 33/33
  parity gate proves agreement with a stale Python. Regenerate expectations
  before trusting it or wiring the worker as a producer.
- Dev OCR fixture drift (Costco/Target) blocks full golden fixture
  regeneration; #1351 appended surgically instead.
