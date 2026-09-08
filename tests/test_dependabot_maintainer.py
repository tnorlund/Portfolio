"""Regression tests for the Dependabot maintainer guardrails."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAINTAINER_PATH = (
    ROOT
    / ".agents"
    / "skills"
    / "dependabot-maintainer"
    / "scripts"
    / "dependabot_maintainer.py"
)


def _load_maintainer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "dependabot_maintainer", MAINTAINER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maintainer = _load_maintainer()


def _which(
    paths: dict[str, str | None],
) -> Callable[[str], str | None]:
    def find_python(command: str) -> str | None:
        return paths.get(command)

    return find_python


def test_python_bin_validates_named_and_fallback_interpreters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misleading python3.13 name cannot bypass the version check."""
    versions = {
        "/tools/python3.13": (3, 12, 11),
        "/tools/python3": (3, 14, 1),
    }
    checked: list[str] = []

    monkeypatch.setattr(
        maintainer.shutil,
        "which",
        _which(
            {
                "python3.13": "/tools/python3.13",
                "python3": "/tools/python3",
            }
        ),
    )
    monkeypatch.setattr(maintainer.sys, "executable", "/current/python")

    def fake_version(python: str) -> tuple[int, int, int]:
        checked.append(python)
        return versions[python]

    monkeypatch.setattr(maintainer, "_python_version", fake_version)

    assert maintainer.python_bin() == "/tools/python3"
    assert checked == ["/tools/python3.13", "/tools/python3"]


def test_python_bin_falls_back_after_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken preferred executable does not hide a valid fallback."""
    monkeypatch.setattr(
        maintainer.shutil,
        "which",
        _which(
            {
                "python3.13": "/broken/python3.13",
                "python3": None,
            }
        ),
    )
    monkeypatch.setattr(maintainer.sys, "executable", "/current/python")

    def fake_version(python: str) -> tuple[int, int, int]:
        if python == "/broken/python3.13":
            raise RuntimeError("probe failed")
        return (3, 13, 7)

    monkeypatch.setattr(maintainer, "_python_version", fake_version)

    assert maintainer.python_bin() == "/current/python"


def test_python_bin_rejects_all_interpreters_below_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallbacks below Python 3.13 fail with actionable diagnostics."""
    monkeypatch.setattr(
        maintainer.shutil,
        "which",
        _which({"python3.13": None, "python3": "/tools/python3"}),
    )
    monkeypatch.setattr(maintainer.sys, "executable", "/current/python")
    monkeypatch.setattr(
        maintainer,
        "_python_version",
        lambda python: {
            "/tools/python3": (3, 12, 10),
            "/current/python": (3, 11, 9),
        }[python],
    )

    with pytest.raises(RuntimeError) as exc_info:
        maintainer.python_bin()

    message = str(exc_info.value)
    assert "Python 3.13 or newer is required" in message
    assert "/tools/python3 is Python 3.12.10" in message
    assert "/current/python is Python 3.11.9" in message
    assert "python3.13 was not found on PATH" in message


def test_python_version_rejects_invalid_probe_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a precise interpreter version response is accepted."""
    monkeypatch.setattr(
        maintainer.shutil,
        "which",
        _which({"python3.13": "/tools/python", "python3": None}),
    )
    monkeypatch.setattr(maintainer.sys, "executable", "/tools/python")
    monkeypatch.setattr(
        maintainer.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Python 3.13\n", stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match="reported an invalid version"):
        maintainer.python_bin()


def test_verify_reports_python_resolution_failure_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The verify command refuses cleanly when no valid Python exists."""
    package_dir = tmp_path / "receipt_dynamo"
    package_dir.mkdir()
    (package_dir / "pyproject.toml").touch()
    pull_request = {
        "headRefOid": "abc123",
        "number": 42,
    }

    monkeypatch.setattr(maintainer, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(maintainer, "pr_view", lambda *args: pull_request)
    monkeypatch.setattr(maintainer, "pr_diff", lambda *args: "")
    monkeypatch.setattr(maintainer, "base_guard_reasons", lambda *args: [])
    monkeypatch.setattr(
        maintainer,
        "major_update_reasons",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        maintainer, "npm_script_change_reasons", lambda *args: []
    )
    monkeypatch.setattr(
        maintainer,
        "changed_paths",
        lambda *args: ["receipt_dynamo/pyproject.toml"],
    )
    monkeypatch.setattr(maintainer, "fetch_pr_ref", lambda *args: "abc123")
    monkeypatch.setattr(
        maintainer, "create_pr_worktree", lambda *args: tmp_path
    )

    def fail_verify(*args: object) -> None:
        raise RuntimeError("Python 3.13 or newer is required")

    monkeypatch.setattr(maintainer, "verify_python_dir", fail_verify)
    args = argparse.Namespace(
        allow_major=False,
        keep_worktree=True,
        pr_number=42,
        repo="owner/repo",
    )

    assert maintainer.command_verify(args) == 1
    assert (
        "Refusing to verify PR #42: Python 3.13 or newer is required"
        in capsys.readouterr().err
    )


def test_coderabbit_is_advisory_but_ci_is_required():
    rabbit = {
        "__typename": "StatusContext",
        "context": "CodeRabbit",
        "state": "PENDING",
    }
    green = {
        "__typename": "CheckRun",
        "name": "Tests",
        "workflowName": "CI/CD Pipeline",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
    }
    assert maintainer.checks_green({"statusCheckRollup": [rabbit, green]})[0]
    assert not maintainer.checks_green({"statusCheckRollup": [rabbit]})[0]
    assert not maintainer.checks_green(
        {"statusCheckRollup": [rabbit, {**green, "conclusion": "FAILURE"}]}
    )[0]


def test_npm_verification_covers_standard_script_aliases(
    tmp_path, monkeypatch
):
    package = tmp_path / "tool"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"scripts": {"typecheck": "tsc", "test": "vitest run"}})
    )
    commands = []
    monkeypatch.setattr(
        maintainer, "run", lambda cmd, **kwargs: commands.append(cmd)
    )
    maintainer.verify_npm_dir(tmp_path, Path("tool"))
    assert commands[-2:] == [
        ["npm", "run", "typecheck"],
        ["npm", "run", "test"],
    ]


@pytest.mark.parametrize("conclusion", ["SKIPPED", "NEUTRAL"])
def test_checks_need_a_successful_project_job(conclusion):
    checks = [
        {
            "__typename": "CheckRun",
            "name": "Tests",
            "workflowName": "CI/CD Pipeline",
            "status": "COMPLETED",
            "conclusion": conclusion,
        },
        {
            "__typename": "CheckRun",
            "name": "GitGuardian",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        },
        {
            "__typename": "StatusContext",
            "context": "CodeRabbit",
            "state": "PENDING",
        },
    ]
    assert not maintainer.checks_green({"statusCheckRollup": checks})[0]


@pytest.mark.parametrize("alias", ["test", "typecheck"])
def test_new_script_aliases_are_guarded(alias):
    patch = f"""diff --git a/tool/package.json b/tool/package.json
--- a/tool/package.json
+++ b/tool/package.json
@@ -1,5 +1,5 @@
 {{
   "scripts": {{
-    "{alias}": "original-check"
+    "{alias}": "changed-check"
   }}
 }}
"""
    assert maintainer.npm_script_change_reasons(patch)
