"""Public exception hierarchy for :mod:`receipt_layoutlm`."""

from __future__ import annotations

from pathlib import Path


class ReceiptLayoutLMError(Exception):
    """Base class for operational failures raised by this package."""


class UnfrozenValidationSplitError(ReceiptLayoutLMError, ValueError):
    """Raised when a run would score itself on a split nobody else used.

    Runs without a pinned canonical validation split still report ``val_f1``
    and per-label metrics that *look* like every other run's, which is how two
    LayoutLM models ended up impossible to compare. Pass ``--val-keys-s3`` to
    hold out the shared frozen split, or ``--no-frozen-val`` to opt out
    explicitly (the run is then stamped ``comparable: false`` everywhere).
    """

    def __init__(self) -> None:
        super().__init__(
            "No frozen validation split configured (val_keys_s3 is unset). "
            "Metrics from a run-local random split are not comparable to any "
            "other run. Pass --val-keys-s3 <s3://.../val_keys.json> to hold "
            "out the shared canonical split, or pass --no-frozen-val to "
            "accept a non-comparable run (its metrics will be stamped "
            "comparable=false in run.json, the Job entity, and the "
            "run_metrics_comparable JobMetric)."
        )


class CheckpointResumeError(ReceiptLayoutLMError):
    """Base class for failures while restoring a training checkpoint."""


class InvalidResumeURIError(CheckpointResumeError, ValueError):
    """Raised when a checkpoint URI is not a usable S3 URI."""


class UnsafeResumeDestinationError(CheckpointResumeError, ValueError):
    """Raised when a job name would write outside the checkpoint root."""


class ResumeDestinationError(CheckpointResumeError):
    """Raised when a local checkpoint destination cannot be prepared."""

    def __init__(self, destination: Path):
        self.destination = destination
        super().__init__(
            f"Unable to prepare local checkpoint destination {destination}"
        )


class ResumeListingError(CheckpointResumeError):
    """Raised when objects at a checkpoint prefix cannot be listed."""


class ResumeDownloadError(CheckpointResumeError):
    """Raised when an individual checkpoint object cannot be downloaded."""

    def __init__(self, *, bucket: str, key: str, destination: Path):
        self.bucket = bucket
        self.key = key
        self.destination = destination
        super().__init__(
            f"Failed to download checkpoint object s3://{bucket}/{key} "
            f"to {destination}"
        )


class CoreMLExportError(ReceiptLayoutLMError):
    """Base class for Core ML export failures."""


class MissingDependencyError(CoreMLExportError):
    """Raised when dependencies required for export are unavailable."""


class NaNWeightsError(CoreMLExportError):
    """Raised when a Core ML export contains non-finite weights.

    This is commonly caused by float16 quantization overflowing values above
    its maximum representable value.
    """

    def __init__(self, bad_count: int, weight_path: Path):
        self.bad_count = bad_count
        self.weight_path = weight_path
        super().__init__(
            f"Exported CoreML model contains {bad_count} NaN/Inf values "
            f"in weight.bin ({weight_path})."
        )
