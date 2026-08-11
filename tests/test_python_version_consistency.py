"""Tests for the repository-wide Python runtime baseline."""

from pathlib import Path

import pytest

import scripts.check_python_version_consistency as checker


def test_python_version_declarations_are_consistent() -> None:
    """Every active package and deployment target should use Python 3.13."""
    assert checker.check_repository() == []


@pytest.mark.parametrize(
    "legacy_reference",
    [
        "python-version: '3." + "12'",
        "Python 3." + "11 is required.",
        "Create the environment with python3." + "10.",
    ],
)
def test_maintained_markdown_is_scanned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_reference: str,
) -> None:
    """Operational Markdown must not preserve an obsolete runtime target."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    guide = tmp_path / "docs" / "development" / "setup.md"
    guide.parent.mkdir(parents=True)
    guide.write_text(f"# Setup\n\n{legacy_reference}\n", encoding="utf-8")

    errors = checker._check_runtime_files()

    assert len(errors) == 1
    assert errors[0].startswith(
        "docs/development/setup.md: legacy Python target"
    )


@pytest.mark.parametrize("historical_root", checker.HISTORICAL_DOCUMENT_ROOTS)
def test_historical_markdown_is_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    historical_root: Path,
) -> None:
    """Frozen historical records may accurately mention retired runtimes."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    record = tmp_path / historical_root / "runtime-record.md"
    record.parent.mkdir(parents=True)
    record.write_text(
        "This snapshot used Python 3." + "12.\n", encoding="utf-8"
    )

    assert checker._check_runtime_files() == []
