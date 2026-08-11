"""Tests for the self-hosted runner Python resolver."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve_python_runtime.sh"


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
