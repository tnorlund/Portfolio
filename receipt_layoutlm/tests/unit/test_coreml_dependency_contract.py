"""Dependency contract for the Python 3.13 CoreML export worker."""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _requirement_names(requirements: list[str]) -> set[str]:
    """Return normalized distribution names without adding a test dependency."""
    return {
        re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0]
        .lower()
        .replace("_", "-")
        for requirement in requirements
    }


def test_coreml_worker_excludes_training_only_sklearn_stack() -> None:
    """The CoreML worker must not install unsupported training metrics."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    base = project["dependencies"]
    extras = project["optional-dependencies"]

    assert "scikit-learn" not in _requirement_names(base)
    assert "seqeval" not in _requirement_names(base)
    assert _requirement_names(extras["coreml"]) == {"coremltools"}
    assert "coremltools==9.0" in extras["coreml"]

    training_names = _requirement_names(extras["training"])
    assert {"scikit-learn", "seqeval"} <= training_names


def test_shared_torch_range_matches_coremltools_9_support() -> None:
    """The shared Torch range must stay within CoreMLtools' tested range."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    torch_requirement = next(
        requirement
        for requirement in project["dependencies"]
        if requirement.startswith("torch")
    )

    assert torch_requirement == "torch>=2.6.0,<=2.7.0"
