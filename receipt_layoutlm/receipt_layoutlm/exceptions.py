"""Public exception hierarchy for :mod:`receipt_layoutlm`."""

from __future__ import annotations

from pathlib import Path


class ReceiptLayoutLMError(Exception):
    """Base class for operational failures raised by this package."""


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
