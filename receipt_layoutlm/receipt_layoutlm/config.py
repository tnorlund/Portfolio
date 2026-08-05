from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from .exceptions import UnfrozenValidationSplitError

# Predefined merge presets for common label grouping scenarios
MERGE_PRESETS: Dict[str, Dict[str, List[str]]] = {
    "amounts": {"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]},
    "date_time": {"DATE": ["TIME"]},
    "address_phone": {"ADDRESS": ["PHONE_NUMBER", "ADDRESS_LINE"]},
    "sroie": {
        # All three combined for SROIE-like 4-label setup
        "AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"],
        "DATE": ["TIME"],
        "ADDRESS": ["PHONE_NUMBER", "ADDRESS_LINE"],
    },
}


@dataclass
class DataConfig:
    dynamo_table_name: str
    aws_region: str = "us-east-1"
    max_seq_length: int = 512
    doc_stride: int = 128
    validation_status: Optional[str] = "VALID"
    allowed_labels: Optional[List[str]] = None
    # Universal label merge configuration: {target_label: [source_labels]}
    label_merges: Optional[Dict[str, List[str]]] = None
    # Legacy field (deprecated, use label_merges instead)
    merge_amounts: bool = False
    dataset_snapshot_load: Optional[str] = None
    dataset_snapshot_save: Optional[str] = None
    # S3 URI of the pinned canonical val split (recorded for run lineage so the
    # Job entity / run.json says which shared val set this run held out).
    val_keys_s3: Optional[str] = None
    # Explicit opt-out from the pinned canonical val split. Without this a run
    # that has no ``val_keys_s3`` fails config validation instead of silently
    # producing numbers that *look* comparable to frozen-split runs. When set,
    # the run proceeds but every metric surface is stamped non-comparable.
    allow_unfrozen_val: bool = False
    # First-pass product/detail experiment: append train-only line-item-band
    # windows while keeping validation and inference on full receipts.
    item_window_augmentation: Optional[bool] = None
    item_window_size: Optional[int] = None
    item_window_stride: Optional[int] = None

    def get_effective_label_merges(self) -> Dict[str, List[str]]:
        """Return the effective label merges, combining explicit config and legacy flags.

        Priority order:
        1. Explicit label_merges config takes precedence
        2. Legacy merge_amounts flag adds AMOUNT merge if not already defined

        Returns:
            Dict mapping target labels to lists of source labels to merge.
        """
        result: Dict[str, List[str]] = {}

        # Apply explicit label_merges first
        if self.label_merges:
            result.update(self.label_merges)

        # Apply legacy merge_amounts if set and AMOUNT not already in label_merges
        if self.merge_amounts and "AMOUNT" not in result:
            result["AMOUNT"] = ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]

        return result

    @property
    def metrics_comparable(self) -> bool:
        """Whether this run's metrics may be compared against other runs.

        Only runs that hold out the *same* frozen receipts produce numbers that
        mean the same thing. A seeded random split makes every run its own
        yardstick.
        """
        return bool(self.val_keys_s3)

    def comparability(self) -> Dict[str, Any]:
        """Return the machine-readable comparability stamp for this run.

        Recorded on ``run.json``, the ``Job`` entity's ``job_config`` and
        ``results``, and as a ``run_metrics_comparable`` JobMetric, so a later
        reader can never mistake a self-split run for a like-for-like number.
        """
        comparable = self.metrics_comparable
        return {
            "comparable": comparable,
            "val_keys_s3": self.val_keys_s3,
            "val_split_source": (
                "frozen_val_keys_s3" if comparable else "seeded_random_split"
            ),
            "reason": (
                f"held out the frozen split {self.val_keys_s3}"
                if comparable
                else (
                    "no frozen validation split (--no-frozen-val); metrics "
                    "are scoped to this run's own random split and must not "
                    "be compared against other runs"
                )
            ),
        }

    def validate_comparability(self) -> None:
        """Fail fast when a run would silently emit non-comparable metrics.

        A run with no ``val_keys_s3`` scores itself on a split nobody else
        used, yet its ``val_f1`` looks exactly like a frozen-split ``val_f1``.
        That ambiguity is what made two LayoutLM models impossible to compare.
        Skipping the frozen split stays possible — a first-ever run on a new
        label vocabulary may have no applicable one — but it must be a
        deliberate, recorded choice rather than an omission.

        Raises:
            UnfrozenValidationSplitError: when neither ``val_keys_s3`` nor the
                explicit ``allow_unfrozen_val`` opt-out is set.
        """
        if self.val_keys_s3 or self.allow_unfrozen_val:
            return
        raise UnfrozenValidationSplitError()


class ModelVersion(str, Enum):
    V1 = "v1"
    V3 = "v3"


MODEL_DEFAULTS = {
    ModelVersion.V1: "microsoft/layoutlm-base-uncased",
    ModelVersion.V3: "microsoft/layoutlmv3-base",
}


@dataclass
class TrainingConfig:
    pretrained_model_name: str = "microsoft/layoutlm-base-uncased"
    batch_size: int = 8
    learning_rate: float = 5e-5
    weight_decay: float = 0.01
    warmup_ratio: float = 0.1
    epochs: int = 10
    mixed_precision: bool = True
    max_grad_norm: float = 1.0
    gradient_accumulation_steps: int = 1
    label_smoothing: float = 0.0
    early_stopping_patience: int = 2
    output_s3_path: Optional[str] = None
    # CoreML export configuration
    auto_export_coreml: bool = False
    coreml_quantize: Optional[str] = "float16"
    model_version: str = ModelVersion.V1.value
    # Metric used by HuggingFace Trainer for best-checkpoint selection and
    # early stopping. Values are normalized to eval_* metric names by trainer.
    checkpoint_metric: str = "f1"
    # Extra class-weight multiplier for product detail labels. This keeps the
    # 22-label first-pass head, but makes product-detail mistakes cost more.
    product_detail_loss_weight: float = 1.0
    # Run the windowed held-out eval in-process after each epoch's checkpoint
    # is saved, emitting epochs.json live (the viz cache) on the training GPU —
    # no separate Processing job needed for new runs. Best-effort: failures are
    # logged and never interrupt training. Disable to skip the per-epoch cost.
    eval_heldout_windowed: bool = True
