#!/usr/bin/env python3
"""Check built function/layer ZIPs against the shared uncompressed budget.

This measures artifacts, not Dockerfile heuristics or dependency estimates.
It does not prove Linux ABI compatibility, handler behavior, or deployment.
"""

import argparse
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

MIB = 1024 * 1024
PROJECT_BUDGET_BYTES = 200 * MIB
AWS_LIMIT_BYTES = 250 * MIB


def measure_archives(paths: list[Path]) -> dict:
    """Sum all uncompressed function and layer entries without extracting."""
    if not paths:
        raise ValueError("Provide the function ZIP and every attached layer")
    artifacts = []
    for path in paths:
        with ZipFile(path) as archive:
            entries = [
                entry for entry in archive.infolist() if not entry.is_dir()
            ]
            if not entries:
                raise ValueError(f"Archive contains no files: {path}")
            artifacts.append(
                {
                    "path": str(path),
                    "files": len(entries),
                    "unzipped_bytes": sum(
                        entry.file_size for entry in entries
                    ),
                }
            )
    size = sum(artifact["unzipped_bytes"] for artifact in artifacts)
    return {
        "artifacts": artifacts,
        "unzipped_bytes": size,
        "project_budget_bytes": PROJECT_BUDGET_BYTES,
        "aws_limit_bytes": AWS_LIMIT_BYTES,
        "within_project_budget": size < PROJECT_BUDGET_BYTES,
        "within_aws_limit": size <= AWS_LIMIT_BYTES,
    }


def main(argv: list[str] | None = None) -> int:
    """Return 0 below the project budget, 1 over budget, 2 for bad inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archives",
        nargs="+",
        type=Path,
        help="Built function ZIP followed by every attached layer ZIP",
    )
    args = parser.parse_args(argv)
    try:
        report = measure_archives(args.archives)
    except (BadZipFile, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if report["within_project_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
