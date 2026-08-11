"""Tests for the repository-wide Python runtime baseline."""

from pathlib import Path

import pytest

import scripts.check_python_version_consistency as checker


def test_python_version_declarations_are_consistent() -> None:
    """Every active package and deployment target should use Python 3.13."""
    assert checker.check_repository() == []


@pytest.mark.parametrize("suffix", sorted(checker.DOCUMENT_SUFFIXES))
@pytest.mark.parametrize(
    "non_baseline_reference",
    [
        "python-version: '3." + "12'",
        "Python 3." + "11 is required.",
        "Create the environment with python3." + "10.",
        "Python 3." + "14 is required.",
        "Python 2." + "7 is unsupported.",
    ],
)
def test_maintained_documentation_is_scanned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    non_baseline_reference: str,
) -> None:
    """Operational documentation must use the repository runtime baseline."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    guide = tmp_path / "docs" / "development" / f"setup{suffix}"
    guide.parent.mkdir(parents=True)
    guide.write_text(f"# Setup\n\n{non_baseline_reference}\n", encoding="utf-8")

    errors = checker._check_runtime_files()

    assert len(errors) == 1
    assert errors[0].startswith(
        f"docs/development/setup{suffix}: non-baseline Python target"
    )


@pytest.mark.parametrize("suffix", sorted(checker.DOCUMENT_SUFFIXES))
def test_python_313_documentation_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    """The active Python 3.13 baseline is valid in every documentation form."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    guide = tmp_path / "docs" / "development" / f"setup{suffix}"
    guide.parent.mkdir(parents=True)
    guide.write_text(
        "Python 3.13+ is required; use python3.13 to create the venv.\n",
        encoding="utf-8",
    )

    assert checker._check_runtime_files() == []


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
    record.write_text("This snapshot used Python 3." + "12.\n", encoding="utf-8")

    assert checker._check_runtime_files() == []
