# synthesis_loop — autonomous Claude-judged hill-climb for synthetic receipts

A long-running loop on the Mac mini that makes synthetic receipts more realistic each round.
**Codex is the brain** (edits params/renderer code to fix the last critique), **headless Claude is
the eyes** (scores realism), **`run_loop.sh` is the heartbeat** (sequences, pushes, sleeps). They run as
sibling processes — Codex never spawns Claude — which is what stops the macOS-sandbox network hang.

Read [`AGENTS.md`](./AGENTS.md) for the full agent contract and the open design decisions.

## Files
| File | Role |
|---|---|
| `AGENTS.md` | Codex's autonomy contract + guardrails (auto-read by Codex) |
| `run_loop.sh` | the heartbeat — run this in `screen` on the mini |
| `judge_round.sh` | headless `claude -p` judge (proven harness from the #1018 smoke test) |
| `score_round.py` | hill-climb bookkeeping → `state/best.json`, `state/STATUS.md` |
| `codex-profile.toml` | the `synthesis-loop` Codex profile (no-approval, sandboxed) |
| `bootstrap_mini.sh` | one-time mini setup (PATH, token, profile, screen launch) |
| `state/params.json` | current renderer knobs the loop is tuning |
| `state/STATUS.md`, `state/best.json`, `state/reviews/` | progress (committed → visible on the PR) |

## Start it (on the mini, once it is reachable)
```bash
cd ~/Portfolio && ./synthesis_loop/bootstrap_mini.sh   # one-time
# then, as printed by bootstrap:
nohup caffeinate -dimsu >/dev/null 2>&1 &
screen -L -dmS synth-loop bash -lc 'cd ~/Portfolio && exec ./synthesis_loop/run_loop.sh'
```

## Watch progress (phone + laptop, no terminal needed)
- **GitHub draft PR for `feat/synthesis-hill-climb`** — every round commits `STATUS.md`, the Claude
  verdict JSON, and curated render thumbnails. Open it in the GitHub mobile app or browser.
- The **Codex code-review bot** and Claude both comment on that PR.
- On the mini directly: `screen -r synth-loop` (detach with `ctrl-a d`) or `tail -f ~/screenlog.0`.

## Stop it
```bash
screen -S synth-loop -X quit ; pkill caffeinate
```
