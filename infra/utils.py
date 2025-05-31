import os
from pathlib import Path


def _find_project_root() -> Path:
    """Find the project root directory by looking for common markers."""
    if os.getenv("GITHUB_WORKSPACE"):
        return Path(os.getenv("GITHUB_WORKSPACE")).resolve()

    current_dir = Path(os.getcwd()).resolve()
    root_markers = [".git", "README.md", "pyproject.toml", ".gitignore"]

    for parent in [current_dir, *current_dir.parents]:
        if any((parent / marker).exists() for marker in root_markers):
            expected_dirs = [
                "receipt_dynamo",
                "receipt_label",
                "receipt_upload",
                "infra",
            ]
            if all((parent / dir_name).is_dir() for dir_name in expected_dirs):
                return parent

    if current_dir.name == "infra":
        parent = current_dir.parent
        expected_dirs = ["receipt_dynamo", "receipt_label", "receipt_upload"]
        if all((parent / dir_name).is_dir() for dir_name in expected_dirs):
            return parent

    return current_dir
