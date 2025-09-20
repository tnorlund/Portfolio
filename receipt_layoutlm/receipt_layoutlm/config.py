from dataclasses import dataclass
from typing import Optional, List


@dataclass
class DataConfig:
    dynamo_table_name: str
    aws_region: str = "us-east-1"
    max_seq_length: int = 512
    doc_stride: int = 128
    validation_status: Optional[str] = "VALID"
    allowed_labels: Optional[List[str]] = None
    merge_amounts: bool = False
    dataset_snapshot_load: Optional[str] = None
    dataset_snapshot_save: Optional[str] = None


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
