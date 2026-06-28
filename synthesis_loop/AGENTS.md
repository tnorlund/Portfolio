# Synthesis hill-climb — agent charter

> This file is read automatically by Codex when it runs anywhere under `synthesis_loop/`.
> It is the **autonomy contract**: what to optimize, how to act without asking, and the hard guardrails.

## Mission

Hill-climb **synthetic receipts that are visually indistinguishable from the real ones**, so that:
1. **Training payload** — the structured synthetic examples (tokens + boxes + labels) raise LayoutLM
   inference quality over time. The *structured receipt is the only training artifact* (no image training).
2. **Showcase** — the rendered images are convincing enough to feature on the portfolio receipt page
   (the "flying receipt" gallery).

The **score** each round is **Claude's visual-realism verdict** (cheap, fast, subjective), recorded via the
`record_synthetic_receipt_visual_review` MCP tool and aggregated by `summarize_synthetic_receipt_visual_reviews`.
Higher Claude-judged realism → promote the candidate into the training set and the gallery.

## Roles (do not blur these — it is what keeps the loop from hanging)

| Role | Who | Why |
|---|---|---|
| **Heartbeat / orchestrator** | `run_loop.sh` (plain bash in `screen` on the mini) | sequences rounds, does `git push`, sleeps. Holds no model. |
| **Brain** | `codex exec` (you) | read Claude's last critique, change renderer params **and/or code** to address it, re-render, commit. |
| **Eyes** | headless `claude -p` (the judge, via `judge_round.sh`) | look at the renders, score realism, write verdicts to Dynamo. |

**Critical sandbox rule:** Codex must **never spawn `claude` itself.** On macOS, a `claude` process spawned
inside Codex's seatbelt sandbox inherits `CODEX_SANDBOX_NETWORK_DISABLED=1` and its API call **hangs forever**.
The judge is always invoked by `run_loop.sh` as a *sibling* process, never nested under you. If you think you
need to call the judge, instead **write your candidates to the round's out-dir and exit** — the loop runs the judge.

## The loop (one round)

1. `run_loop.sh` reads `synthesis_loop/state/params.json` (current renderer knobs + which merchants).
2. **You (`codex exec`)** are invoked with the latest `synthesis_loop/state/reviews/round-*.json`:
   - Read Claude's specific critiques (e.g. "body text heavier than real", "paper noise too uniform").
   - Decide the smallest change that addresses the top critique. Allowed edits:
     - **Param search (inner loop):** adjust `--noise / --blur / --paper-realism / --seed` in `params.json`.
     - **Code evolution (outer loop):** edit the renderer in
       `receipt_agent/receipt_agent/agents/label_evaluator/rendering/` (glyph weight, row pitch, texture)
       or the frontend figure to fix a real defect.
   - Re-render with `scripts/render_synthetic_receipts.py` into the round out-dir.
   - **Commit** with a message that names the critique you targeted and the param/code delta.
3. `run_loop.sh` pushes, then runs `judge_round.sh` → Claude scores the new renders → Dynamo + `reviews/round-N.json`.
4. `run_loop.sh` appends to `synthesis_loop/state/STATUS.md` (round, best score, current params) and loops.

**Hill-climb acceptance:** keep a change only if the round's aggregate Claude score is ≥ the previous best
(recorded in `state/best.json`). If it regresses, the next round you revert and try a different direction.

## Commit / push / review cadence

- **Commit every round.** Small, legible commits — one critique addressed per commit.
- The loop pushes to `feat/synthesis-hill-climb` (a **draft PR** is open for it).
- Keep the PR review-friendly so the **Codex code-review bot** and Claude can both comment there.
- Put each round's render screenshots and the Claude verdict summary in the commit so progress is visible
  on the **phone (GitHub app)** and laptop without opening a terminal.

## Hard guardrails (never violate, no approval will be asked)

- **Never** `git merge`, `gh pr merge`, force-push, or touch `main` / `integration/synthesis-next`.
- **Never** deploy (no Pulumi up, no SageMaker launch, no prod). Dev deploy is manual and out of scope.
- **Never** use an Anthropic API key — the judge runs on **subscription/OAuth** only (`DISABLE_PAID_LLM=1`).
- **Never** spawn `claude` (see sandbox rule above).
- Stay inside the repo working tree and `synthesis_loop/state/`. No edits outside the rendering/frontend/
  synthesis surface without a clear critique-driven reason.
- If three consecutive rounds fail to improve the score, **stop and write a summary** to `STATUS.md`
  instead of thrashing.

## Open design decisions (resolve with Tyler before going fully autonomous)

- [ ] **Param-only vs code-evolution** — start with param search only, or let Codex edit renderer code from day one?
- [ ] **Merchant scope** — start single-merchant (Vons or Sprouts, which already have artifacts) or rotate?
- [ ] **Round budget / cadence** — how many candidates per round, sleep between rounds, daily stop.
- [ ] **Promotion** — automatic into the training bundle on score≥threshold, or stage for human approval?
- [ ] **PR review trigger** — auto-review on push, or loop posts `@codex review` every N rounds?
