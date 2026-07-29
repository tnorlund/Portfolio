"""Receipt LayoutLM training package."""

from receipt_layoutlm.exceptions import (
    CheckpointResumeError,
    CoreMLExportError,
    InvalidResumeURIError,
    MissingDependencyError,
    NaNWeightsError,
    ReceiptLayoutLMError,
    ResumeDestinationError,
    ResumeDownloadError,
    ResumeListingError,
    UnsafeResumeDestinationError,
)

__all__ = [
    "CheckpointResumeError",
    "CoreMLExportError",
    "InvalidResumeURIError",
    "MissingDependencyError",
    "NaNWeightsError",
    "ReceiptLayoutLMError",
    "ResumeDestinationError",
    "ResumeDownloadError",
    "ResumeListingError",
    "UnsafeResumeDestinationError",
]


# Lazy attribute access to avoid import errors if optional deps aren't installed
def __getattr__(name):
    if name == "DataConfig" or name == "TrainingConfig":
        from .config import DataConfig, TrainingConfig  # type: ignore

        return {"DataConfig": DataConfig, "TrainingConfig": TrainingConfig}[
            name
        ]
    if name == "ReceiptLayoutLMTrainer":
        from .trainer import ReceiptLayoutLMTrainer  # type: ignore

        return ReceiptLayoutLMTrainer
    if name == "LayoutLMInference":
        from .inference import LayoutLMInference  # type: ignore

        return LayoutLMInference
    raise AttributeError(name)
