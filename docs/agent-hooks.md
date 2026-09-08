# Agent hook contract

`scripts/agent-hooks/guard-shell.py` recognizes common direct Pulumi, Git,
AWS CLI, and owner-only script invocations. It is an additional check against
the repository defaults. It is not a shell interpreter, sandbox, or substitute
for runtime permissions and the user's explicit authorization.

It handles attached shell operators, common wrappers and their options,
Git global options/refspecs, direct interpreter options, and literal `cd`
changes. It does not analyze arbitrary script bodies, aliases, dynamically
constructed commands, shell expansion, or every possible wrapper. Do not
interpret an allow result as proof that an operation is safe or authorized.

## Payloads and activation

- Cursor `beforeShellExecution` supplies `command` and `cwd`. The response
  contains `permission: allow` or `deny`.
- Claude Code `PreToolUse` supplies `tool_input.command` and `cwd`. A denied
  command receives `hookSpecificOutput.permissionDecision: deny`.
- Codex hooks normalize shell calls, including `exec_command`, to `Bash`
  and `tool_input.command`. The configured `Bash` matcher is intentional;
  it is not the name of the model-facing execution tool. See
  [the official Codex hook contract](https://learn.chatgpt.com/docs/hooks).
- Codex project hooks require a supported host and trust review. Committing
  `.codex/hooks.json` does not establish that hooks are active on every host.
  Host trust and settings are not changed by this repository PR.

Unknown or malformed payloads fail open. The unit/protocol tests call the
scripts with documented payloads and inspect their replies without executing
the proposed shell command. They do not claim a live hook invocation by each
desktop application's installed version.

The formatter accepts Cursor file edits, Claude file-edit payloads, and Codex
`apply_patch` payloads. It formats existing Python files inside the checkout
only, using available Black/isort binaries. Missing tools are a no-op.

Run the offline contract tests with:

```sh
python3.13 -m pytest tests/test_agent_hooks.py
```
