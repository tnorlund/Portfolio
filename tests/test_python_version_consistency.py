"""Tests for the repository-wide Python runtime baseline."""

import tomllib
from pathlib import Path

import pytest

import scripts.check_python_version_consistency as checker


def test_python_version_declarations_are_consistent() -> None:
    """Package tooling keeps its baseline while container docs may name 3.14."""
    assert checker.check_repository() == []


@pytest.mark.parametrize("suffix", sorted(checker.DOCUMENT_SUFFIXES))
@pytest.mark.parametrize(
    "non_baseline_reference",
    [
        "python-version: '3." + "12'",
        "Python 3." + "11 is required.",
        "Create the environment with python3." + "10.",
        "Python 3." + "14 is required.",
        "Python 3." + "15 is required.",
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
    guide.write_text(
        f"# Setup\n\n{non_baseline_reference}\n", encoding="utf-8"
    )

    errors = checker._check_runtime_files()

    assert len(errors) == 1
    assert errors[0].startswith(
        f"docs/development/setup{suffix}: non-baseline Python target"
    )


@pytest.mark.parametrize("suffix", sorted(checker.DOCUMENT_SUFFIXES))
def test_supported_runtime_documentation_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    """General setup documentation uses the repository baseline."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    guide = tmp_path / "docs" / "development" / f"setup{suffix}"
    guide.parent.mkdir(parents=True)
    guide.write_text(
        "Python 3.13 is required; use python3.13 for this target.\n",
        encoding="utf-8",
    )

    assert checker._check_runtime_files() == []


def test_container_runtime_exception_is_limited_to_reviewed_guide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    guide = tmp_path / "AGENTS.md"
    guide.write_text("Baseline Python 3.13; containers use Python 3.14.\n")
    assert checker._check_runtime_files() == []
    guide.write_text("Baseline Python 3.13; containers use Python 3.15.\n")
    assert len(checker._check_runtime_files()) == 1


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


@pytest.mark.parametrize(
    "ignored_directory",
    sorted(checker.IGNORED_DIRECTORY_NAMES) + [".venv", ".venv-python"],
)
def test_generated_pyprojects_are_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignored_directory: str,
) -> None:
    """Generated third-party pyprojects must not be parsed or reported."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    pyproject = (
        tmp_path
        / "nested"
        / ignored_directory
        / "dependency"
        / "pyproject.toml"
    )
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text(
        "[project\nmalformed generated TOML", encoding="utf-8"
    )

    assert checker._check_pyprojects() == []


def test_maintained_pyprojects_are_still_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignored-path filtering must not weaken maintained package checks."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    old_version = "3." + "12"
    old_target = "py" + "312"
    pyproject = tmp_path / "maintained" / "pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'name = "maintained"',
                f'requires-python = ">={old_version}"',
                "classifiers = [",
                f'  "Programming Language :: Python :: {old_version}",',
                "]",
                "",
                "[tool.black]",
                f'target-version = ["{old_target}"]',
            ]
        ),
        encoding="utf-8",
    )

    errors = checker._check_pyprojects()

    assert len(errors) == 3
    assert any("requires-python" in error for error in errors)
    assert any("Python classifiers" in error for error in errors)
    assert any("Black target-version" in error for error in errors)


def test_malformed_maintained_pyproject_is_not_silently_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only generated paths may bypass TOML parsing."""
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    pyproject = tmp_path / "maintained" / "pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text(
        "[project\nmalformed maintained TOML", encoding="utf-8"
    )

    with pytest.raises(tomllib.TOMLDecodeError):
        checker._check_pyprojects()
