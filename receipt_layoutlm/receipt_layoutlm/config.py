from dataclasses import dataclass
from typing import Optional


@dataclass
class DataConfig:
    dynamo_table_name: str
    aws_region: str = "us-east-1"
    max_seq_length: int = 512
    doc_stride: int = 128
    validation_status: Optional[str] = "VALID"


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
    output_s3_path: Optional[str] = None
