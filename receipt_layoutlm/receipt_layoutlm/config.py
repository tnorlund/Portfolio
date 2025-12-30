from dataclasses import dataclass, field
from typing import Optional, List, Dict


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
