#!/usr/bin/env python3
"""
Create a minimal build context for Docker builds.

This script creates a temporary directory with only the files needed for
the build, avoiding Docker context hash changes from unrelated files.
"""

import shutil
import tempfile
from pathlib import Path
from typing import List, Optional


def create_minimal_context(
    packages: List[str],
    dockerfile_path: Path,
    base_path: Optional[Path] = None,
) -> Path:
    """
    Create a minimal build context containing only specified packages.

    Args:
        packages: List of package names to include (e.g., ["receipt_dynamo"])
        dockerfile_path: Path to the Dockerfile
        base_path: Base path where packages are located (defaults to repo root)

    Returns:
        Path to the temporary build context directory
    """
    if base_path is None:
        base_path = Path(__file__).parent.parent.parent

    # Create a temporary directory for the build context
    temp_dir = Path(tempfile.mkdtemp(prefix="docker-build-context-"))

    try:
        # Copy only the required packages
        for package in packages:
            src = base_path / package
            if src.exists():
                dst = temp_dir / package
                print(f"Copying {package} to build context...")
                shutil.copytree(
                    src,
                    dst,
                    ignore=shutil.ignore_patterns(
                        "__pycache__",
                        "*.pyc",
                        "*.pyo",
                        ".pytest_cache",
                        "tests",
                        "*.egg-info",
                        ".coverage",
                        "htmlcov",
                        "*.log",
                        ".DS_Store",
                    ),
                )

        # Copy the Dockerfile to the context
        dockerfile_dst = temp_dir / "Dockerfile"
        shutil.copy2(dockerfile_path, dockerfile_dst)

        # Create a marker file to indicate this is a minimal context
        (temp_dir / ".minimal-context").touch()

        return temp_dir

    except Exception as e:
        # Clean up on error
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise e


def cleanup_context(context_path: Path):
    """Clean up a temporary build context."""
    if context_path.exists() and context_path.name.startswith(
        "docker-build-context-"
    ):
        shutil.rmtree(context_path, ignore_errors=True)


if __name__ == "__main__":
    # Example usage
    context = create_minimal_context(
        packages=["receipt_dynamo"],
        dockerfile_path=Path("dockerfiles/Dockerfile.receipt_dynamo"),
    )
    print(f"Created minimal context at: {context}")

    # Show what's in the context
    for item in context.rglob("*"):
        if item.is_file():
            rel_path = item.relative_to(context)
            size = item.stat().st_size
            print(f"  {rel_path} ({size:,} bytes)")

    # Cleanup
    input("Press Enter to cleanup...")
    cleanup_context(context)
