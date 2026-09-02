#!/usr/bin/env python3.13
"""Classify container Dockerfiles for a post-Chroma zip-Lambda follow-up.

AWS Lambda zip packages (function + layers) cannot exceed 250 MB unzipped.
Chroma's native tree is what pushed receipt Lambdas onto container images;
after Phase 4 teardown that tree goes away and most images become zip-sized.

This module does not flip ``PackageType``. It only classifies Dockerfiles so
a conversion PR can wait until ``receipt_chroma`` is gone from the image and
can skip LayoutLM / SageMaker (PyTorch).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Hard AWS limit for zip packaging (function + layers, unzipped).
ZIP_UNZIPPED_LIMIT_MB = 250

# Measured 2026-09-02 in the Cloud Agent CPython 3.13 x86_64 venv.
# Directionally correct; a conversion PR must re-measure on Amazon Linux 2023
# Python 3.13 arm64 (Lambda's runtime) before flipping PackageType.
CHROMA_STACK_UNZIPPED_MB = 252
REMAINING_FAT_UNZIPPED_MB = 153

BUCKET_CHROMA_BLOCKED = "chroma_blocked"
BUCKET_ALREADY_SLIM = "already_slim"
BUCKET_STAY_IMAGE = "stay_image"
BUCKET_NOT_LAMBDA = "not_lambda"

_DOCKERFILE_GLOB = "infra/**/Dockerfile*"


@dataclass(frozen=True)
class DockerfileClass:
    """One container build context and why it is or is not zip-eligible."""

    relative_path: str
    bucket: str
    reason: str


def _dockerfile_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def classify_dockerfile(path: Path, *, root: Path) -> DockerfileClass:
    """Return the zip-eligibility bucket for one Dockerfile."""

    relative = str(path.relative_to(root))
    text = _dockerfile_text(path)
    lowered = text.lower()

    if "sagemaker_training" in relative:
        return DockerfileClass(
            relative_path=relative,
            bucket=BUCKET_NOT_LAMBDA,
            reason="SageMaker training image, not a Lambda",
        )
    if "torch==" in lowered or "pytorch/pytorch" in lowered:
        return DockerfileClass(
            relative_path=relative,
            bucket=BUCKET_STAY_IMAGE,
            reason="PyTorch does not fit the 250 MB zip ceiling",
        )
    if (
        "receipt_chroma/" in text
        or "/tmp/receipt_chroma" in text
        or "chromadb" in lowered
    ):
        return DockerfileClass(
            relative_path=relative,
            bucket=BUCKET_CHROMA_BLOCKED,
            reason=(
                "Image still installs receipt_chroma; zip would include "
                "the ~250 MB ONNX/Rust/Kubernetes tree"
            ),
        )
    return DockerfileClass(
        relative_path=relative,
        bucket=BUCKET_ALREADY_SLIM,
        reason="No Chroma or PyTorch install; zip-sized today",
    )


def iter_dockerfiles(root: Path | None = None) -> list[Path]:
    """Return infra Dockerfiles, stable-sorted."""

    base = root if root is not None else REPOSITORY_ROOT
    return sorted(base.glob(_DOCKERFILE_GLOB))


def classify_repository(root: Path | None = None) -> list[DockerfileClass]:
    """Classify every infra Dockerfile."""

    base = root if root is not None else REPOSITORY_ROOT
    return [
        classify_dockerfile(path, root=base) for path in iter_dockerfiles(base)
    ]


def remaining_fat_fits_zip() -> bool:
    """True when the post-Chroma fat dep set is under the zip ceiling."""

    return REMAINING_FAT_UNZIPPED_MB < ZIP_UNZIPPED_LIMIT_MB


def chroma_stack_exceeds_zip() -> bool:
    """True when the Chroma native tree alone blows the zip ceiling."""

    return CHROMA_STACK_UNZIPPED_MB >= ZIP_UNZIPPED_LIMIT_MB


def format_table(rows: list[DockerfileClass]) -> str:
    """Render a human-readable classification table."""

    lines = [
        f"{'bucket':16} {'dockerfile'}",
        f"{'-' * 16} {'-' * 40}",
    ]
    for row in rows:
        lines.append(f"{row.bucket:16} {row.relative_path}")
        lines.append(f"{'':16} {row.reason}")
    return "\n".join(lines)


def main() -> int:
    """Print the classification table and the size-budget check."""

    rows = classify_repository()
    print(format_table(rows))
    print()
    print(
        f"chroma stack ~{CHROMA_STACK_UNZIPPED_MB} MB unzipped; "
        f"limit {ZIP_UNZIPPED_LIMIT_MB} MB; "
        f"exceeds={chroma_stack_exceeds_zip()}"
    )
    print(
        f"post-chroma fat path ~{REMAINING_FAT_UNZIPPED_MB} MB unzipped; "
        f"fits_zip={remaining_fat_fits_zip()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
