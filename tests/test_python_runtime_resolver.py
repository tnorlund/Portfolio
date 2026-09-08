"""Tests for the self-hosted runner Python resolver."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve_python_runtime.sh"
ENSURE_RUNTIME = ROOT / "scripts" / "ensure_python_runtime.sh"


def test_resolver_returns_the_exact_running_minor() -> None:
    """The resolver returns an interpreter with the requested minor."""
    required_minor = f"{sys.version_info.major}.{sys.version_info.minor}"

    result = subprocess.run(
        [RESOLVER, required_minor],
        check=True,
        capture_output=True,
        text=True,
    )
    resolved_python = Path(result.stdout.strip())

    assert resolved_python.is_file()
    version = subprocess.run(
        [
            resolved_python,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert version.stdout.strip() == required_minor


def test_resolver_rejects_an_unavailable_minor() -> None:
    """The resolver fails clearly instead of falling back to another minor."""
    result = subprocess.run(
        [RESOLVER, "9.99"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Python 9.99 not found" in result.stderr


def test_ensure_runtime_serializes_homebrew_bootstrap(
    tmp_path: Path,
) -> None:
    """Concurrent runners install a missing interpreter only once."""
    ready_file = tmp_path / "runtime-ready"
    brew_arguments = tmp_path / "brew-arguments"
    fake_resolver = tmp_path / "resolve-python"
    fake_brew = tmp_path / "brew"

    fake_resolver.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ -f "$FAKE_RUNTIME_READY" ]]; then\n'
        "  printf '/managed/python%s\\n' \"$1\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_brew.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_BREW_ARGUMENTS"\n'
        "sleep 0.2\n"
        'touch "$FAKE_RUNTIME_READY"\n'
        'exit "${FAKE_BREW_STATUS:-0}"\n',
        encoding="utf-8",
    )
    fake_resolver.chmod(0o755)
    fake_brew.chmod(0o755)

    environment = {
        **dict(os.environ),
        "FAKE_RUNTIME_READY": str(ready_file),
        "FAKE_BREW_ARGUMENTS": str(brew_arguments),
        "FAKE_BREW_STATUS": "17",
        "HOMEBREW_BIN": str(fake_brew),
        "PYTHON_RUNTIME_RESOLVER": str(fake_resolver),
        "PYTHON_INSTALL_LOCK_ROOT": str(tmp_path),
        "PYTHON_INSTALL_POLL_SECONDS": "0.05",
    }
    with subprocess.Popen(
        [ENSURE_RUNTIME, "3.13"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    ) as first_process, subprocess.Popen(
        [ENSURE_RUNTIME, "3.13"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    ) as second_process:
        processes = [first_process, second_process]
        results = [process.communicate(timeout=5) for process in processes]

    assert [process.returncode for process in processes] == [0, 0]
    assert [stdout.strip() for stdout, _ in results] == [
        "/managed/python3.13",
        "/managed/python3.13",
    ]
    assert brew_arguments.read_text(encoding="utf-8").strip() == (
        "install python@3.13"
    )
