#!/usr/bin/env python3.14
"""Verify the Python 3.14 baseline and the LayoutLM 3.13 runtime carve-out."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.14"
PYTHON_TARGET = "py314"
# The oldest runtime still deployed. Only the LayoutLM containers run it
# (their pinned torch has no 3.14 wheels), and they install receipt_dynamo,
# so shared packages keep a >=3.13 floor while every other runtime, CI leg
# and tool target sits on the 3.14 baseline.
PYTHON_FLOOR = "3.13"
PYTHON_FLOOR_TARGET = "py" + PYTHON_FLOOR.replace(".", "")
# Files that legitimately name the LayoutLM 3.13 runtime. General setup
# guides and every other runtime file must say 3.14.
SECONDARY_RUNTIME_DOCUMENTS = {
    Path("AGENTS.md"),
    Path("receipt_layoutlm/README.md"),
    Path(".agents/skills/coreml-export/SKILL.md"),
}
SECONDARY_RUNTIME_FILES = {
    Path("infra/routes/layoutlm_inference_cache_generator/lambdas/Dockerfile"),
    Path("infra/sagemaker_training/Dockerfile"),
    Path("tests/test_lambda_image_inventory.py"),
    Path("tests/test_sagemaker_training_runtime.py"),
}
# receipt_layoutlm is packaged for the 3.13 image, so its tooling targets
# the floor rather than the baseline.
SECONDARY_RUNTIME_PYPROJECTS = {Path("receipt_layoutlm/pyproject.toml")}

SCAN_ROOTS = (
    ".github",
    ".agents/skills",
    "infra",
    "scripts",
    "synthesis_loop",
    "tests",
    "receipt_upload/tests",
)
SCAN_SUFFIXES = {".py", ".sh", ".yaml", ".yml"}
SCAN_NAMES = {
    ".gitignore",
    ".pre-commit-config.yaml",
    "pyrightconfig.json",
}
DOCUMENT_SUFFIXES = {".adoc", ".markdown", ".md", ".mdx", ".rst"}

# Documentation outside these locations is maintained operational guidance.
# These directories contain frozen handoffs, review evidence, or explicitly
# archived material whose version
# references describe the repository at an earlier point in time.
HISTORICAL_DOCUMENT_ROOTS = (
    Path(".review-loop"),
    Path("docs/archive"),
    Path("docs/handoff"),
    Path("docs/handoffs"),
    Path("docs/review-loops"),
)

# Repository-local environments and generated dependency trees can contain
# third-party Markdown that is not maintained by this repository.
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}

# This cleanup script names already-deployed Python 3.12 resources. Those
# physical identifiers must remain intact until the old resources are removed.
LEGACY_RESOURCE_FILES = {
    Path("infra/scripts/cleanup_receipt_label_layer.sh"),
}

OLD_VERSION_TOKEN = re.compile(
    r"(?:"
    r"\bpython3\.(?:8|9|10|11|12|13)\b|"
    r"\bpython(?:38|39|310|311|312|313)\b|"
    r"\bpy(?:38|39|310|311|312|313)\b|"
    r"\bpython:3\.(?:8|9|10|11|12|13)\b|"
    r"\bpython@3\.(?:8|9|10|11|12|13)\b|"
    r"Versions/3\.(?:8|9|10|11|12|13)\b"
    r")"
)
OLD_VERSION_DECLARATION = re.compile(
    r"(?:python[-_]version|python_versions)" r"[^\n]*3\.(?:8|9|10|11|12|13)\b",
    re.IGNORECASE,
)
NON_BASELINE_DOCUMENT_VERSION_TOKEN = re.compile(
    r"\bpython\s*(?:(?:>=?|==|~=|[:@])\s*)?" + r"(?:2\.\d+|3\.(?!14\b)\d+)\b",
    re.IGNORECASE,
)
# Tokens naming the LayoutLM runtime, stripped from the carved-out files
# before the scans above run.
SECONDARY_VERSION_TOKEN = re.compile(
    r"(?:"
    r"\bpython\s*(?:(?:>=?|==|~=|[:@])\s*)?3\.13\b|"
    r"\bpy(?:thon)?313\b|"
    r"(?:python[-_]version|python_versions)[^\n]*3\.13\b"
    r")",
    re.IGNORECASE,
)
PYTHON_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")


def _is_scannable(path: Path) -> bool:
    return path.name.startswith("Dockerfile") or path.suffix in SCAN_SUFFIXES


def _is_document(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_SUFFIXES


def _is_ignored_path(path: Path) -> bool:
    relative = _relative(path)
    return any(
        part in IGNORED_DIRECTORY_NAMES or part.startswith(".venv")
        for part in relative.parts
    )


def _is_historical_document(path: Path) -> bool:
    relative = _relative(path)
    return any(
        relative == root or root in relative.parents
        for root in HISTORICAL_DOCUMENT_ROOTS
    )


def _active_runtime_files() -> list[Path]:
    paths = [REPOSITORY_ROOT / name for name in SCAN_NAMES]
    for root_name in SCAN_ROOTS:
        root = REPOSITORY_ROOT / root_name
        if not root.exists():
            continue
        paths.extend(path for path in root.rglob("*") if _is_scannable(path))

    paths.extend(
        path
        for path in REPOSITORY_ROOT.rglob("*")
        if path.is_file()
        and _is_document(path)
        and not _is_ignored_path(path)
        and not _is_historical_document(path)
    )
    return sorted({path for path in paths if path.is_file()})


def _relative(path: Path) -> Path:
    return path.relative_to(REPOSITORY_ROOT)


def _check_runtime_files() -> list[str]:
    errors: list[str] = []
    for path in _active_runtime_files():
        relative = _relative(path)
        if relative in LEGACY_RESOURCE_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if relative in SECONDARY_RUNTIME_DOCUMENTS | SECONDARY_RUNTIME_FILES:
            text = SECONDARY_VERSION_TOKEN.sub("", text)
        patterns = [OLD_VERSION_TOKEN, OLD_VERSION_DECLARATION]
        if _is_document(path):
            patterns.append(NON_BASELINE_DOCUMENT_VERSION_TOKEN)
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                matched_text = match.group(0)
                errors.append(
                    f"{relative}: non-baseline Python target "
                    f"{matched_text!r}"
                )
                break
    return errors


def _check_tool_version(
    errors: list[str],
    relative: Path,
    tool_name: str,
    value: object,
    expected: object,
) -> None:
    if value is not None and value != expected:
        errors.append(
            f"{relative}: {tool_name} is {value!r}; expected {expected!r}"
        )


def _check_pyprojects() -> list[str]:
    errors: list[str] = []
    for path in sorted(REPOSITORY_ROOT.rglob("pyproject.toml")):
        if _is_ignored_path(path):
            continue
        relative = _relative(path)
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        project = data.get("project")
        if project is None:
            continue
        secondary = relative in SECONDARY_RUNTIME_PYPROJECTS
        expected_version = PYTHON_FLOOR if secondary else PYTHON_VERSION
        expected_target = PYTHON_FLOOR_TARGET if secondary else PYTHON_TARGET
        requires_python = str(project.get("requires-python"))
        if not (
            requires_python.startswith(f">={PYTHON_FLOOR}")
            or requires_python.startswith(f">={PYTHON_VERSION}")
        ):
            errors.append(
                f"{relative}: requires-python is {requires_python!r}; "
                f"expected a >={PYTHON_FLOOR} or >={PYTHON_VERSION} floor"
            )
        classifiers = project.get("classifiers", [])
        version_classifiers = {
            match.group(1)
            for classifier in classifiers
            if (match := PYTHON_CLASSIFIER.match(classifier))
        }
        # Shared packages may advertise the floor as well (the LayoutLM
        # image installs them), but must name the runtime they deploy on.
        allowed = {PYTHON_FLOOR, expected_version}
        if version_classifiers and (
            expected_version not in version_classifiers
            or not version_classifiers <= allowed
        ):
            errors.append(
                f"{relative}: Python classifiers are "
                f"{sorted(version_classifiers)!r}; expected "
                f"{expected_version} (optionally with {PYTHON_FLOOR})"
            )
        tools = data.get("tool", {})
        _check_tool_version(
            errors,
            relative,
            "Black target-version",
            tools.get("black", {}).get("target-version"),
            [expected_target],
        )
        _check_tool_version(
            errors,
            relative,
            "Ruff target-version",
            tools.get("ruff", {}).get("target-version"),
            expected_target,
        )
        _check_tool_version(
            errors,
            relative,
            "mypy python_version",
            tools.get("mypy", {}).get("python_version"),
            expected_version,
        )
    return errors


def check_repository() -> list[str]:
    """Return every Python-version consistency error in the repository."""
    errors: list[str] = []
    version_file = REPOSITORY_ROOT / ".python-version"
    pinned_version = version_file.read_text(encoding="utf-8").strip()
    if pinned_version != PYTHON_VERSION:
        errors.append(
            f".python-version is {pinned_version!r}; expected "
            f"{PYTHON_VERSION!r}"
        )
    errors.extend(_check_runtime_files())
    errors.extend(_check_pyprojects())
    return errors


def main() -> int:
    """Print consistency errors and return a shell-friendly status code."""
    errors = check_repository()
    if errors:
        print("Python version declarations are inconsistent:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"Python {PYTHON_VERSION} baseline and LayoutLM {PYTHON_FLOOR} carve-out declarations are consistent."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
