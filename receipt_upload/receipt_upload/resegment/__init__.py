"""Safe, line-first receipt re-segmentation planning and previews."""

from receipt_upload.resegment.planner import (
    ResegmentPlanError,
    build_source_fingerprint,
    compute_plan_hash,
    normalize_line_resegmentation_plan,
    normalize_resegmentation_plan,
)
from receipt_upload.resegment.preview import build_preview_bundle

__all__ = [
    "ResegmentPlanError",
    "build_source_fingerprint",
    "build_preview_bundle",
    "compute_plan_hash",
    "normalize_line_resegmentation_plan",
    "normalize_resegmentation_plan",
]
