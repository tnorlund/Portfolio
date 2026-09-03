# Portfolio Remote Control Runbook

## Target Sessions

Use this exact mapping unless the user gives an explicit replacement:

| Label | Worktree | Branch | Remote name | Screen name |
| --- | --- | --- | --- | --- |
| `merchant-intel` | `~/Portfolio/.claude/worktrees/merchant-intel` | `feat/merchant-intelligence-agents` | `merchant-intel` | `claude-merchant-intel-rc` |
| `font-render` | `~/Portfolio/.claude/worktrees/font-render` | `feat/receipt-font-render` | `font-render` | `claude-font-render-rc` |
| `orchestration` | `~/Portfolio/.claude/worktrees/orchestration` | `feat/synthesis-orchestration` | `orchestration` | `claude-orchestration-rc` |

Each worktree should contain committed `CONTEXT.md` and `CHARTER.md` files. The remote Claude sessions should read those files before doing implementation work.

## Reliable Startup Sequence

The durable path is:

1. Clean only the target Portfolio sessions:
   - Quit target `screen` sessions.
   - Kill only Claude processes whose command line includes one of the three target remote-control names.
   - Run `screen -wipe`.
2. Start each Claude inside a detached `screen -L -dmS ...` from `/tmp/claude-remote-screen/<label>`.
3. Attach with a real PTY using `TERM=xterm-256color screen -x <screen-name>`.
4. Respond to prompts:
   - Bypass permissions warning: send `2` then Enter.
   - Fullscreen renderer prompt: send `2` then Enter.
   - Folder trust prompt: accept.
5. Wait for the prompt box after `/remote-control is active`.
6. Send bracketed paste: `ESC [ 200 ~`, mission prompt, `ESC [ 201 ~`.
7. Send Enter as a separate write.
8. Wait for signs of work, then detach with Ctrl-A then `d`.
9. Open Terminal.app attachers so the user has visible GUI Terminal windows/tabs.
10. Start a skill-managed `caffeinate -dimsu /bin/sleep 86400` unless the skill's PID file already points to a live keep-awake process.

## Known Pitfalls

- Avoid `screen -X stuff` for Claude TUI input. It can appear to send keys but not actually reach the prompt correctly on this Mac's old `screen`.
- Avoid Down-arrow plus Enter for the bypass warning. Numeric `2` is more reliable.
- Avoid writing the long mission prompt and Enter in one operation. Use bracketed paste first, then Enter.
- Avoid broad `pkill -f 'claude.*remote-control'` unless the user explicitly wants every remote-control session killed. This machine may have unrelated remote-control sessions.
- Old macOS `screen` does not support `-Logfile`; use `screen -L` and expect `screenlog.0` in the screen's working directory.

## Verification Commands

```bash
ps axww -o pid= -o command= | grep '[c]laude .*--remote-control'
screen -ls
pgrep -fl 'caffeinate.*-dimsu'
scripts/portfolio_remote_control.sh status
```

Successful launch means all three remote-control processes are alive, all three screens exist, `status` reports `log_evidence=1` from normalized tool-use text after the mission prompt was submitted, and the user can see `merchant-intel`, `font-render`, and `orchestration` in the Claude mobile app.

## Supervising the cadence

The review-first commit/push cadence must be **supervised**, not just stated. Its
presence in each worktree's `CONTEXT.md` makes an agent *intend* it; it does not
enforce it. Run `portfolio_remote_control.sh status` periodically:

- `cadence=DRIFT` for a session means it is hoarding uncommitted WIP (more than
  `PORTFOLIO_RC_DIRTY_WARN`, default 8, changed files) or sitting on unpushed
  commits. Re-nudge it to codex-review → commit → push before it accumulates more
  (a session reached 20 uncommitted files this way).
- The "Unexpected remote-control sessions" block lists any remote-control claude
  sessions that are not the trio — close strays so they don't compete for
  resources or confuse status.
- `check_push_auth` runs at launch; if it warns, fix gh/remote on the mini before
  the agents try to push, or their work strands locally on the box.

## Launch flow (updated) — positional mission + human-accept checkpoint

`launch` now starts each session with its mission **pre-loaded as a positional
prompt** (`claude … --remote-control <name> "<mission>"`) instead of pasting it
into the TUI afterward. The fragile bracketed-paste step is gone. The flow:

1. `launch` cleans targets, starts the screens (mission pre-loaded), opens
   Terminal attachers, caffeinates, and prints accept instructions.
2. **You accept the bypass-permissions prompt in each Terminal** (`Down` ->
   `Yes, I accept` -> `Enter`). The mission runs automatically on accept.

That single human accept per agent is intentional — it is the deliberate
checkpoint for starting an autonomous bypass-permissions session, so the launcher
does **not** auto-suppress or auto-accept the warning. The manual-paste steps
below remain only as a fallback for re-sending a mission to an already-running
session that was launched without one.

### Registering sessions beyond the default trio

`status`, `cleanup`, and stray-detection all read `entries()`. To add a new
branch-agent so it is tracked (and not flagged as an "unexpected stray"), set:

```
export PORTFOLIO_RC_EXTRA_SESSIONS="name:branch [name2:branch2 ...]"
# e.g.
export PORTFOLIO_RC_EXTRA_SESSIONS="glyph-rendering:feat/receipt-glyph-rendering"
```

Worktree path, session name, and screen name are derived by convention
(`.claude/worktrees/<name>`, `<name>`, `claude-<name>-rc`).
