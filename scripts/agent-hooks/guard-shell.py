#!/usr/bin/env python3
"""Shell-command guard shared by Cursor and Claude Code hooks.

Enforces the hard rules from AGENTS.md that instructions alone cannot:

- No Pulumi against the prod stack, and no mutating Pulumi command that is
  not pinned to ``--stack tnorlund/portfolio/dev``.
- No ``git push --force`` (any spelling) and no push to ``main``.
- No commits made while ``main`` is checked out.
- No mutating DynamoDB CLI calls against the prod table.

Input (stdin JSON) is either Cursor's ``beforeShellExecution`` payload
(``{"command": ..., "cwd": ...}``) or Claude Code's ``PreToolUse`` payload
(``{"tool_name": "Bash", "tool_input": {"command": ...}, "cwd": ...}``).

Output: Cursor gets ``{"permission": "allow"|"deny", ...}`` on exit 0; Claude
Code gets the reason on stderr with exit 2 (its documented block signal).
Anything unrecognised is allowed so the hook fails open.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable

DEV_STACK = "tnorlund/portfolio/dev"
PROD_TABLE = "ReceiptsTable-d7ff76a"
PULUMI_MUTATING = {
    "up",
    "update",
    "destroy",
    "refresh",
    "import",
    "preview",
    "cancel",
    "select",
    "rm",
    "init",
    "set",
    "set-all",
    "state",
    "new",
    "convert",
}
GIT_FORCE_FLAGS = {
    "-f",
    "--force",
    "--force-with-lease",
    "--force-if-includes",
}
DYNAMO_MUTATING = {
    "put-item",
    "update-item",
    "delete-item",
    "batch-write-item",
    "transact-write-items",
    "delete-table",
    "update-table",
    "execute-statement",
    "batch-execute-statement",
}
SEPARATORS = {"&&", "||", ";", "|", "&"}


def _tokenize(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    out: list[str] = []
    for tok in tokens:
        # shlex keeps "a;b" together when unquoted; split trailing separators.
        if tok in SEPARATORS:
            out.append(tok)
            continue
        if tok.endswith(";") and len(tok) > 1:
            out.extend([tok[:-1], ";"])
            continue
        out.append(tok)
    return out


def _segments(tokens: Iterable[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in SEPARATORS or tok == "\n":
            segments.append([])
        else:
            segments[-1].append(tok)
    return [seg for seg in segments if seg]


def _strip_env_prefix(seg: list[str]) -> list[str]:
    i = 0
    while i < len(seg) and "=" in seg[i] and not seg[i].startswith("-"):
        i += 1
    return seg[i:]


def _basename(tok: str) -> str:
    return Path(tok).name


def _has_flag_value(seg: list[str], flags: set[str], value: str) -> bool:
    for i, tok in enumerate(seg):
        if tok in flags and i + 1 < len(seg) and seg[i + 1] == value:
            return True
        if any(tok == f"{flag}={value}" for flag in flags):
            return True
    return False


def _current_branch(cwd: str | None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _check_pulumi(seg: list[str]) -> str | None:
    if "prod" in seg or any("/prod" in tok or "prod/" in tok for tok in seg):
        return (
            "Production is a hard no-go: Pulumi commands that name the prod "
            "stack are blocked."
        )
    if any(tok in PULUMI_MUTATING for tok in seg[1:]):
        if not _has_flag_value(seg, {"--stack", "-s"}, DEV_STACK):
            return (
                "Pulumi mutations must be pinned to the fully qualified dev "
                f"stack: add `--stack {DEV_STACK}` (and preview before up)."
            )
    return None


def _check_git(seg: list[str], cwd: str | None, full: list[str]) -> str | None:
    if len(seg) < 2:
        return None
    sub = seg[1]
    if sub == "push":
        args = seg[2:]
        if any(tok in GIT_FORCE_FLAGS for tok in args) or any(
            tok.startswith("+") for tok in args
        ):
            return "Force-pushing is not allowed in this repository."
        refspecs = [tok for tok in args if not tok.startswith("-")]
        for ref in refspecs:
            if ref == "main" or ref.endswith(":main"):
                return "Pushing to main is not allowed; push a feature branch."
        if len(refspecs) <= 1 and _current_branch(cwd) == "main":
            return "You are on main; create a feature branch before pushing."
    if sub == "commit":
        if _current_branch(cwd) == "main":
            return "Never commit directly to main; create a feature branch."
        switched_to_main = any(
            full[i] in {"checkout", "switch"}
            and i + 1 < len(full)
            and full[i + 1] == "main"
            for i in range(len(full))
        )
        if switched_to_main:
            return "This command switches to main and commits; use a branch."
    return None


def _check_aws(seg: list[str]) -> str | None:
    if "dynamodb" not in seg[1:3]:
        return None
    if PROD_TABLE in seg and any(tok in DYNAMO_MUTATING for tok in seg):
        return f"Writes to the prod table {PROD_TABLE} are blocked."
    return None


def evaluate(command: str, cwd: str | None) -> str | None:
    """Return a denial reason, or None when the command is allowed."""
    tokens = _tokenize(command.replace("\n", " \n "))
    for raw_seg in _segments(tokens):
        seg = _strip_env_prefix(raw_seg)
        if not seg:
            continue
        prog = _basename(seg[0])
        if prog in {"sudo", "env", "time", "nohup"} and len(seg) > 1:
            seg = _strip_env_prefix(seg[1:])
            if not seg:
                continue
            prog = _basename(seg[0])
        reason = None
        if prog == "pulumi":
            reason = _check_pulumi(seg)
        elif prog == "git":
            reason = _check_git(seg, cwd, tokens)
        elif prog == "aws":
            reason = _check_aws(seg)
        if reason:
            return reason
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0

    tool_input = payload.get("tool_input")
    is_claude = isinstance(tool_input, dict)
    if is_claude:
        command = tool_input.get("command")
    else:
        command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        if not is_claude:
            print(json.dumps({"permission": "allow"}))
        return 0

    reason = evaluate(command, payload.get("cwd"))
    if reason is None:
        if not is_claude:
            print(json.dumps({"permission": "allow"}))
        return 0

    message = f"Blocked by scripts/agent-hooks/guard-shell.py: {reason}"
    if is_claude:
        print(message, file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "permission": "deny",
                "user_message": message,
                "agent_message": (
                    f"{message} See the Hard rules section of AGENTS.md."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
