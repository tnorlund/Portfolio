# Synthesis hill-climb — agent charter

> This file is read automatically by Codex when it runs anywhere under `synthesis_loop/`.
> It is the **autonomy contract**: what to optimize, how to act without asking, and the hard guardrails.

## Mission

Hill-climb **synthetic receipts that are visually indistinguishable from the real ones**, so that:
1. **Training payload** — the structured synthetic examples (tokens + boxes + labels) raise LayoutLM
   inference quality over time. The *structured receipt is the only training artifact* (no image training).
2. **Showcase** — the rendered images are convincing enough to feature on the portfolio receipt page
   (the "flying receipt" gallery).

The **score** each round is **Claude's verdict**, recorded via `record_synthetic_receipt_visual_review` and
aggregated by `summarize_synthetic_receipt_visual_reviews`. Claude scores **two axes**, because LayoutLM trains
on the *structured* receipt (tokens+boxes+labels), not the pixels:
- **Texture realism** (paper noise, blur, thermal look) → gates the **gallery** + makes review trustworthy.
  Does not by itself change training data.
- **Structural plausibility** (row pitch, glyph metrics, box placement, plausible item lines, tax math that
  adds up) → this is the part that flows into **training quality**, because the training boxes track the layout.

**Promotion rule (hard):** higher realism → gallery. A candidate enters the **training bundle ONLY if it also
passes the existing quality gates** (#1001/#1003 layout-integrity + arithmetic). A real-*looking* render with
wrong labels must never reach training. Realism never overrides the gates.

## Roles (do not blur these — it is what keeps the loop from hanging)

| Role | Who | Why |
|---|---|---|
| **Heartbeat / orchestrator** | `run_loop.sh` (plain bash in `screen` on the mini) | sequences rounds, does `git push`, sleeps. Holds no model. |
| **Brain** | `codex exec` (you) | read Claude's last critique, change renderer params **and/or code** to address it, re-render, commit. |
| **Eyes** | headless `claude -p` (the judge, via `judge_round.sh`) | **full tools** (Read/Glob/Bash) + receipt-tools MCP. Reads the `*.real_vs_synthetic.png` composites to actually SEE them, reads the structured candidate to check arithmetic/labels, scores two axes, writes verdicts to Dynamo. Runs `opus` (all review steps use opus). |

**Critical sandbox rule:** Codex must **never spawn `claude` itself.** On macOS, a `claude` process spawned
inside Codex's seatbelt sandbox inherits `CODEX_SANDBOX_NETWORK_DISABLED=1` and its API call **hangs forever**.
The judge is always invoked by `run_loop.sh` as a *sibling* process, never nested under you. If you think you
need to call the judge, instead **write your candidates to the round's out-dir and exit** — the loop runs the judge.

## The loop (one round)

1. `run_loop.sh` reads `params.json` and sets the round's active `merchant` = `merchants[round % len]` (rotation).
2. **You (`codex exec`)** are invoked with the round's merchant and the latest `state/reviews/round-*.json`:
   - Read the judge's specific critiques and `top_fixes`.
   - Make the highest-impact change to **texture realism** that will ACTUALLY alter the rendered pixels.
   - **EDIT-LAYER CONSTRAINT (critical — this is cached render mode):** you may edit ONLY
     - `synthesis_loop/state/params.json` (`noise / blur / paper_realism / seed / glyph knobs`), and
     - the glyph renderer: `receipt_agent/receipt_agent/agents/label_evaluator/rendering/`
       (`glyph_atlas.py`, `glyph_renderer.py`, `glyph_ttf_fallback.py`).
     **Do NOT edit `sprouts_parameterization.py` or any `synthesis/*.py`** — cached render never executes them,
     so those edits are invisible no-ops on the image. (Structural fixes wait for bundle mode.)
   - **Do NOT render, push, call AWS, or spawn claude** — the shell renders (it has the Dynamo glyph atlas).
   - **Commit** naming the change. If your edit doesn't change the pixels, the loop's no-op guard perturbs a
     knob for you — but aim to make a real change.
3. `run_loop.sh` renders (shell), runs the no-op/hash guard, pushes, then `judge_round.sh` (opus; lean each
   round, deep 5-lens panel every `PANEL_EVERY`) writes its OWN per-round contract to `reviews/round-N.json`.
4. `score_round.py` = mean of THIS run/round's `texture_realism` (no cumulative fallback) → `best.json` /
   `STATUS.md`, scoped to `run_id`. On regression, `params.json` is rolled back to the best so far.

**Hill-climb acceptance:** keep a change only if the round's aggregate Claude score is ≥ the previous best
(recorded in `state/best.json`). If it regresses, the next round you revert and try a different direction.

## Two DISTINCT reviewers — do not conflate them

1. **Local image/code judge** = headless Claude (`judge_round.sh`, opus). Looks at the *rendered images* +
   structure each round and writes realism verdicts to Dynamo. This is the hill-climb's score signal.
2. **GitHub Codex code-review bot** = `chatgpt-codex-connector[bot]` in the cloud. Reviews the *PR code diff*
   and leaves inline `path:line` findings with P1/P2/P3 badges. This is about code quality, not renders.

`run_loop.sh` drives reviewer #2 on a cycle every `REVIEW_EVERY` rounds via `review_cycle()`:
- **Respond first:** the shell fetches the bot's inline findings to `state/codex_review_comments.json`;
  **you (codex) read that file and apply the P1/P2 fixes** (edit + commit only — the shell pushes and replies).
- **Then request:** the shell posts `@codex review` for the latest batch and flags it pending.

When you are the review-responder, your contract: apply minimal fixes for in-scope P1/P2 findings, note any
you skip and why, run the render-verify guard if you touch renderer code, commit referencing the findings, and
**do NOT push, call gh, reply on the PR, spawn claude, or merge/deploy** — the shell owns all network/PR actions.

## Commit / push / review cadence

- **Commit every round.** Small, legible commits — one critique addressed per commit.
- The loop pushes to `feat/synthesis-hill-climb` (draft PR **#1022**).
- Commit `STATUS.md`, the Claude verdict JSON, and `state/gallery/round-N.png` so progress is visible on the
  **phone (GitHub app)** and laptop without a terminal.

## Hard guardrails (never violate, no approval will be asked)

- **Never** `git merge`, `gh pr merge`, force-push, or touch `main` / `integration/synthesis-next`.
- **Never** deploy (no Pulumi up, no SageMaker launch, no prod). Dev deploy is manual and out of scope.
- **Never** use an Anthropic API key — the judge runs on **subscription/OAuth** only (`DISABLE_PAID_LLM=1`).
- **Never** spawn `claude` (see sandbox rule above).
- Stay inside the repo working tree and `synthesis_loop/state/`. No edits outside the rendering/frontend/
  synthesis surface without a clear critique-driven reason.
- If three consecutive rounds fail to improve the score, **stop and write a summary** to `STATUS.md`
  instead of thrashing.

## Decisions (resolved with Tyler 2026-06-28)

- [x] **Code-evolution, but only in the render layer** — Codex may edit `params.json` + the glyph renderer
      (`…/rendering/`). NOT `sprouts_parameterization`/synthesis (cached render never runs it → invisible no-op).
      The shell renders, guards against no-op/broken renders, and perturbs a knob if pixels didn't change.
- [x] **Rotate merchants** — `params.json.merchants` = ["Sprouts","Vons"]; `run_loop.sh` rotates per round.
- [x] **Cadence** — defaults: 4 candidates/round, 90s sleep, stop after 3 no-improve rounds or 50 rounds total.
- [x] **Promotion** — realism gates the gallery; the existing #1001/#1003 quality gates are the hard floor for
      what enters the LayoutLM training bundle. Realism never overrides the gates.
- [x] **PR review** — every 5 rounds `run_loop.sh` responds to the prior Codex-bot review (codex applies the
      P1/P2 fixes → shell pushes + replies) then requests a fresh `@codex review`. See "Two DISTINCT reviewers".

## Wiring note (verify on first live run — mini was offline at build time)

The judge reads renders by the `local_image_path` returned from `list_synthetic_receipt_visual_review_targets`.
That path must match where `render_synthetic_receipts.py --out-dir` writes (`state/renders/round-N/`). On the
first real round, confirm the MCP's computed `local_image_path` points at the round's out-dir; if not, align the
render out-dir (or the server's base dir) so the judge sees the candidates it is scoring.
