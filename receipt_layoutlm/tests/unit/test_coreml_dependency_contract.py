"""Dependency contracts for CoreML / Core AI export extras."""

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


def _torch_requirement(requirements: list[str]) -> str:
    return next(
        requirement
        for requirement in requirements
        if requirement.startswith("torch")
    )


def test_coreml_worker_excludes_training_only_sklearn_stack() -> None:
    """The CoreML worker must not install unsupported training metrics."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    base = project["dependencies"]
    extras = project["optional-dependencies"]

    assert "scikit-learn" not in _requirement_names(base)
    assert "seqeval" not in _requirement_names(base)
    assert _requirement_names(extras["coreml"]) == {
        "coremltools",
        "torch",
    }
    assert "coremltools==9.0" in extras["coreml"]
    assert "scikit-learn" not in _requirement_names(extras["coreml"])
    assert "seqeval" not in _requirement_names(extras["coreml"])

    training_names = _requirement_names(extras["training"])
    assert {"scikit-learn", "seqeval"} <= training_names


def test_base_torch_is_not_capped_by_coremltools() -> None:
    """Main LayoutLM installs must not inherit the CoreML torch ceiling."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    torch_requirement = _torch_requirement(project["dependencies"])

    assert torch_requirement == "torch>=2.6.0,<3.0.0"
    assert "<=2.7" not in torch_requirement


def test_coreml_extra_keeps_torch_within_coremltools_9_support() -> None:
    """[coreml] must pin torch to the CoreMLtools-tested range."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    extras = project["optional-dependencies"]
    torch_requirement = _torch_requirement(extras["coreml"])

    assert torch_requirement == "torch>=2.6.0,<=2.7.0"


def test_coreai_extra_requires_modern_torch_and_coreai_torch() -> None:
    """[coreai] must pull coreai-torch with torch>=2.8 and no sklearn."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    extras = project["optional-dependencies"]
    coreai = extras["coreai"]
    names = _requirement_names(coreai)

    assert names == {"coreai-torch", "torch"}
    assert "scikit-learn" not in names
    assert "seqeval" not in names
    assert "coreai-torch" in coreai

    torch_requirement = _torch_requirement(coreai)
    assert torch_requirement.startswith("torch>=2.8.0")
    assert "<=2.13.0" in torch_requirement
    assert "<=2.7" not in torch_requirement


def test_python_314_classifier_is_declared() -> None:
    """Advertise Python 3.14 support for the main LayoutLM package."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    assert "Programming Language :: Python :: 3.14" in project["classifiers"]
