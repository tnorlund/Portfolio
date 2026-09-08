#!/usr/bin/env python3
"""Shell-command guard shared by Cursor, Claude Code, and Codex hooks.

Checks common direct CLI forms against the defaults in AGENTS.md:

- No Pulumi against the prod stack, and no mutating Pulumi command that is
  not pinned to ``--stack tnorlund/portfolio/dev``.
- No ``git push --force`` (any spelling) and no push to ``main``.
- No commits made while ``main`` is checked out.
- No mutating DynamoDB CLI calls against the prod table.
- No owner-only scripts: dev→prod sync/promote, merchant-truth activation,
  prod ingestion, or any ``--live`` flag.

Input (stdin JSON) is either Cursor's ``beforeShellExecution`` payload
(``{"command": ..., "cwd": ...}``) or the ``PreToolUse`` payload shared by
Claude Code and Codex
(``{"tool_name": "Bash", "tool_input": {"command": ...}, "cwd": ...}``).

This is an advisory command guard, not a shell interpreter or a security
boundary. It cannot inspect arbitrary script bodies, shell aliases, dynamic
expansion, or commands executed through another program. Agent instructions
and runtime permissions still apply when a command is not recognized.

Output (always exit 0): Cursor gets ``{"permission": "allow"|"deny", ...}``;
Claude Code and Codex get the ``hookSpecificOutput.permissionDecision`` JSON
that both document, plus the reason on stderr. Anything unrecognised is
allowed so the hook fails open.
"""

from __future__ import annotations

import json
import re
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
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")"}
# Owner-only scripts (matched on the script's basename, any interpreter).
OWNER_ONLY_SCRIPT_PARTS = ("dev_to_prod", "promote_", "_prod.sh")
OWNER_ONLY_SCRIPTS = {
    "activate_merchant_truth.py",
    "mint_merchant_truth_v2.py",
    "promote_merchant_truth.py",
    "start_ingestion_prod.sh",
}
INTERPRETERS = {"python", "python3", "python3.13", "bash", "sh", "zsh"}


def _tokenize(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def _segments(tokens: Iterable[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok and all(char in ";&|()\n" for char in tok):
            segments.append([])
        else:
            segments[-1].append(tok)
    return [seg for seg in segments if seg]


def _strip_env_prefix(seg: list[str]) -> list[str]:
    i = 0
    while i < len(seg) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]):
        i += 1
    return seg[i:]


def _unwrap(seg: list[str], cwd: str | None) -> tuple[list[str], str | None]:
    """Skip common env/sudo/time/nohup options before the real command."""
    value_options = {
        "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
        "sudo": {
            "-u",
            "--user",
            "-g",
            "--group",
            "-h",
            "--host",
            "-p",
            "--prompt",
            "-C",
            "--close-from",
            "-D",
            "--chdir",
        },
        "time": {"-o", "--output", "-f", "--format"},
        "nohup": set(),
    }
    seg = _strip_env_prefix(seg)
    while seg and _basename(seg[0]) in value_options:
        wrapper = _basename(seg[0])
        options = value_options[wrapper]
        i = 1
        split_command: list[str] = []
        while i < len(seg) and seg[i].startswith("-"):
            token = seg[i]
            i += 1
            if token == "--":
                break
            if token in options:
                if i < len(seg):
                    if (
                        token in {"-C", "--chdir"}
                        and wrapper == "env"
                        or token in {"-D", "--chdir"}
                        and wrapper == "sudo"
                    ):
                        directory = Path(seg[i]).expanduser()
                        cwd = str(
                            directory
                            if directory.is_absolute()
                            else Path(cwd or ".") / directory
                        )
                    if token in {"-S", "--split-string"} and wrapper == "env":
                        split_command = _tokenize(seg[i])
                i += 1
            elif token.startswith("--chdir="):
                directory = Path(token.split("=", 1)[1]).expanduser()
                cwd = str(
                    directory
                    if directory.is_absolute()
                    else Path(cwd or ".") / directory
                )
        seg = _strip_env_prefix([*split_command, *seg[i:]])
    return seg, cwd


def _git_args(seg: list[str]) -> tuple[list[str], list[str]]:
    """Find the Git subcommand and honor its directory-selection options."""
    i = 1
    while i < len(seg) and seg[i].startswith("-"):
        token = seg[i]
        i += 1
        if token in {
            "-C",
            "--work-tree",
            "--git-dir",
            "-c",
            "--config-env",
            "--namespace",
        }:
            i += 1
    return [seg[0], *seg[i:]], seg[1:i]


def _basename(tok: str) -> str:
    return Path(tok).name


def _has_flag_value(seg: list[str], flags: set[str], value: str) -> bool:
    for i, tok in enumerate(seg):
        if tok in flags and i + 1 < len(seg) and seg[i + 1] == value:
            return True
        if any(tok == f"{flag}={value}" for flag in flags):
            return True
    return False


def _current_branch(
    cwd: str | None, git_options: list[str] | None = None
) -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                *(git_options or []),
                "symbolic-ref",
                "--short",
                "-q",
                "HEAD",
            ],
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
    if "cancel" in seg[1:]:
        return "Never interrupt an active Pulumi update on a shared stack."
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
    seg, git_options = _git_args(seg)
    if len(seg) < 2:
        return None
    sub = seg[1]
    if sub == "push":
        args = seg[2:]
        if any(
            tok.split("=", 1)[0] in GIT_FORCE_FLAGS
            or (
                tok.startswith("-") and not tok.startswith("--") and "f" in tok
            )
            for tok in args
        ) or any(tok.startswith("+") for tok in args):
            return "Force-pushing is not allowed in this repository."
        if any(tok in {"--all", "--mirror"} for tok in args):
            return "Push one explicit feature branch, not all branches."
        refspecs = [tok for tok in args if not tok.startswith("-")]
        for ref in refspecs:
            destination = ref.rsplit(":", 1)[-1].removeprefix("refs/heads/")
            if destination == "main" or "*" in destination:
                return "Pushing to main is not allowed; push a feature branch."
        if len(refspecs) <= 1 and _current_branch(cwd, git_options) == "main":
            return "You are on main; create a feature branch before pushing."
    if sub == "commit":
        if _current_branch(cwd, git_options) == "main":
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
    value_options = {
        "--profile",
        "--region",
        "--endpoint-url",
        "--output",
        "--query",
        "--ca-bundle",
        "--cli-read-timeout",
        "--cli-connect-timeout",
        "--color",
    }
    i = 1
    while i < len(seg) and seg[i].startswith("-"):
        token = seg[i]
        i += 1
        if token in value_options:
            i += 1
    if i >= len(seg) or seg[i] != "dynamodb":
        return None
    if PROD_TABLE in " ".join(seg) and any(
        tok in DYNAMO_MUTATING for tok in seg[i + 1 :]
    ):
        return f"Writes to the prod table {PROD_TABLE} are blocked."
    return None


def _check_owner_only(seg: list[str]) -> str | None:
    if any(token.split("=", 1)[0] == "--live" for token in seg):
        return "`--live` runs are owner-only; use the dry-run default."
    candidates = seg[:1]
    if _basename(seg[0]) in INTERPRETERS and len(seg) > 1:
        i = 1
        while i < len(seg) and seg[i].startswith("-"):
            token = seg[i]
            i += 1
            if token == "--":
                break
            if token in {"-W", "-X", "-o", "-O"} and i < len(seg):
                i += 1
        candidates = seg[i : i + 1]
    for tok in candidates:
        name = _basename(tok)
        if name in OWNER_ONLY_SCRIPTS or any(
            part in name for part in OWNER_ONLY_SCRIPT_PARTS
        ):
            return (
                f"`{name}` promotes or syncs to prod and is owner-only; "
                "agents never run it."
            )
    return None


def evaluate(command: str, cwd: str | None) -> str | None:
    """Return a denial reason, or None when the command is allowed."""
    tokens = _tokenize(command)
    for raw_seg in _segments(tokens):
        seg, segment_cwd = _unwrap(raw_seg, cwd)
        if not seg:
            continue
        prog = _basename(seg[0])
        if prog == "cd" and len(seg) > 1:
            target = seg[-1]
            if target != "-" and not target.startswith("$"):
                directory = Path(target).expanduser()
                cwd = str(
                    directory
                    if directory.is_absolute()
                    else Path(cwd or ".") / directory
                )
        reason = _check_owner_only(seg)
        if reason is None and prog == "pulumi":
            reason = _check_pulumi(seg)
        elif reason is None and prog == "git":
            reason = _check_git(seg, segment_cwd, tokens)
        elif reason is None and prog == "aws":
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

    if not isinstance(payload, dict):
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
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": message,
                    }
                }
            )
        )
        print(message, file=sys.stderr)
        return 0
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
