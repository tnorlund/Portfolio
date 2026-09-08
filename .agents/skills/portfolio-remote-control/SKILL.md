---
name: portfolio-remote-control
description: Launch and verify the three autonomous Claude Code remote-control sessions for the Portfolio repo. Use when the user asks Codex or Claude to turn on, start, restart, supervise, or verify the Portfolio remote-control trio for merchant intelligence, receipt font rendering, and synthesis orchestration worktrees.
---

# Portfolio Remote Control

## Purpose

Use this skill to start the three Portfolio Claude Code remote-control sessions and get them past the interactive bypass warning into real autonomous work. The supported sessions are:

| Label | Worktree | Remote-control name |
| --- | --- | --- |
| `merchant-intel` | `~/Portfolio/.claude/worktrees/merchant-intel` | `merchant-intel` |
| `font-render` | `~/Portfolio/.claude/worktrees/font-render` | `font-render` |
| `orchestration` | `~/Portfolio/.claude/worktrees/orchestration` | `orchestration` |

The mission prompt to send to each session is fixed:

```text
Read CONTEXT.md then CHARTER.md in this worktree. They hold the full context from the session that set this branch up plus your specific mission. Follow the charter milestones in order and self-review with codex along the way exactly as CONTEXT.md mandates. Begin with milestone 1 now.
```

## Fast Path

Resolve paths relative to this `SKILL.md`, then run:

```bash
scripts/portfolio_remote_control.sh launch
```

This helper:

1. Cleans only the three target Portfolio remote-control sessions.
2. Starts one detached `screen` session per worktree with `/opt/homebrew/bin/claude --permission-mode bypassPermissions --remote-control <name>`.
3. Pre-loads each session's mission as a **positional prompt** at launch (no fragile TUI paste). You then accept the bypass-permissions prompt in each Terminal (press `2` -> `Yes, I accept`, then `Enter`); the mission runs automatically on accept. That one human accept per agent is the deliberate checkpoint for starting an autonomous bypass-permissions session.
4. Opens Terminal.app attachers for the three screens when a GUI session is available.
5. Starts a skill-managed durable `caffeinate -dimsu /bin/sleep 86400` if one is not already alive.
6. Prints process, screen, and log status.

Use these focused commands as needed:

```bash
scripts/portfolio_remote_control.sh status
scripts/portfolio_remote_control.sh prime
scripts/portfolio_remote_control.sh start
scripts/portfolio_remote_control.sh cleanup
```

## Verification Contract

Before reporting success, verify all of the following:

- `ps` shows all three `claude --permission-mode bypassPermissions --remote-control ...` processes with names `merchant-intel`, `font-render`, and `orchestration`.
- `screen -ls` shows the three target screens: `claude-merchant-intel-rc`, `claude-font-render-rc`, and `claude-orchestration-rc`.
- Each screen log under `/tmp/claude-remote-screen/<label>/screenlog.0` shows actual tool use, such as `Reading 1 file`, `Bash(`, `Edit(`, or `TodoWrite`; the echoed mission prompt alone is not success evidence.
- A `caffeinate -dimsu` process is alive, preferably the skill-managed 24-hour helper recorded under `/tmp/claude-remote-screen/caffeinate.pid`.
- Tell the user that the three Claude sessions should now appear by name in the Claude app on their phone for remote control.

## Manual Fallback

If the helper starts the screens but cannot prime one, attach with a real PTY. Do not use `screen -X stuff`; it can fail to deliver input to Claude's TUI on this Mac.

```bash
TERM=xterm-256color screen -x claude-merchant-intel-rc
TERM=xterm-256color screen -x claude-font-render-rc
TERM=xterm-256color screen -x claude-orchestration-rc
```

Inside each attached screen:

1. If the bypass warning appears, type `2` then Enter. Prefer numeric `2`; Down arrow can select the wrong option in this TUI.
2. If the fullscreen renderer prompt appears, type `2` then Enter for "Not now".
3. If a folder trust prompt appears, accept it.
4. After `/remote-control is active` and the prompt box is ready, bracket-paste the full mission prompt, then send Enter as a separate input.
5. Detach with Ctrl-A then `d`, leaving the session running.

The separate paste and submit matter. Sending the long prompt plus Enter as one raw write can leave the prompt unsubmitted or drop characters.

## Details

For a fuller runbook and troubleshooting notes, read `references/portfolio-remote-control-runbook.md`.
