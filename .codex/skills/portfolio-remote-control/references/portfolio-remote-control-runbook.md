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
grep -aE 'CONTEXT\.md|CHARTER\.md|Read\(|Bash\(|Edit\(|TodoWrite' /tmp/claude-remote-screen/*/screenlog.0
```

Successful launch means all three remote-control processes are alive, all three screens exist, the logs show the sessions moved past warnings into work, and the user can see `merchant-intel`, `font-render`, and `orchestration` in the Claude mobile app.
