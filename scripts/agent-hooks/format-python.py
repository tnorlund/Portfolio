#!/usr/bin/env python3
"""Format Python files an agent just edited, matching the CI contract.

CI runs ``black --line-length=79`` and ``isort --profile=black
--line-length=79`` per package; "format to CI contract" has been the single
most repeated fix commit in this repo, so this hook applies both formatters
to each edited ``.py`` file right after the edit.

Input (stdin JSON) is one of:

- Cursor ``afterFileEdit``: ``{"file_path": "<abs path>", ...}``
- Claude Code ``PostToolUse`` for Edit/Write/MultiEdit:
  ``{"tool_input": {"file_path": ...}, "cwd": ...}``
- Codex ``PostToolUse`` for ``apply_patch``:
  ``{"tool_input": {"command": "*** Update File: path ..."}, "cwd": ...}``

Only files inside the repository are touched; ``portfolio/``, virtualenvs,
``node_modules``, and non-Python files are skipped. Missing formatters are a
no-op. Always exits 0 and prints nothing so no tool treats it as feedback.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKIP_PARTS = {"portfolio", "node_modules", ".git", "docs", "__pycache__"}
PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add) File: (.+)$", re.M)


def _formatter(name: str) -> str | None:
    venv_bin = REPO_ROOT / ".venv" / "bin" / name
    if venv_bin.exists():
        return str(venv_bin)
    try:
        subprocess.run(
            [name, "--version"], capture_output=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return name


def _candidate_paths(payload: dict) -> list[str]:
    paths: list[str] = []
    if isinstance(payload.get("file_path"), str):
        paths.append(payload["file_path"])
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        if isinstance(tool_input.get("file_path"), str):
            paths.append(tool_input["file_path"])
        command = tool_input.get("command")
        if isinstance(command, str) and "*** Begin Patch" in command:
            paths.extend(m.strip() for m in PATCH_FILE_RE.findall(command))
    return paths


def _eligible(raw: str, cwd: Path) -> Path | None:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    try:
        path = path.resolve()
        rel = path.relative_to(REPO_ROOT)
    except (OSError, ValueError):
        return None
    if path.suffix != ".py" or not path.is_file():
        return None
    if any(
        part in SKIP_PARTS or part.startswith(".venv") for part in rel.parts
    ):
        return None
    return path


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    cwd = Path(payload.get("cwd") or REPO_ROOT)
    files = []
    for candidate in _candidate_paths(payload):
        path = _eligible(candidate, cwd)
        if path and path not in files:
            files.append(path)
    if not files:
        return 0

    black = _formatter("black")
    isort = _formatter("isort")
    targets = [str(p) for p in files]
    if black:
        subprocess.run(
            [black, "-q", "--line-length=79", *targets],
            capture_output=True,
            timeout=60,
            check=False,
        )
    if isort:
        subprocess.run(
            [isort, "-q", "--profile=black", "--line-length=79", *targets],
            capture_output=True,
            timeout=60,
            check=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
