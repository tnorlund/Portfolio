#!/usr/bin/env python3
"""Run strict checks for the native trace writer and cache builders."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infra/native-tracing-quality.toml"


def main() -> None:
    """Use one source list and fail immediately when a check fails."""
    with CONFIG.open("rb") as source:
        paths = tomllib.load(source)["tool"]["mypy"]["files"]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(ROOT / path)
            for path in (
                ".",
                "receipt_dynamo",
                "receipt_upload",
                "infra/routes/label_validation_viz_cache/handlers",
            )
        ),
    }
    # Respect each package's existing formatter configuration.
    for package in ("infra", "receipt_upload", "scripts"):
        selected = [path for path in paths if path.startswith(f"{package}/")]
        for command in (
            ["black", "--check", "--line-length=79"],
            ["isort", "--check-only", "--profile=black", "--line-length=79"],
        ):
            subprocess.run(
                [sys.executable, "-m", *command, *selected],
                cwd=ROOT,
                env=env,
                check=True,
            )
    for command in (
        ["mypy", "--config-file", str(CONFIG)],
        ["pylint", "--rcfile", str(CONFIG), *paths],
    ):
        subprocess.run(
            [sys.executable, "-m", *command],
            cwd=ROOT,
            env=env,
            check=True,
        )


if __name__ == "__main__":
    main()
