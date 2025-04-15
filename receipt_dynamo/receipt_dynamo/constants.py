from enum import Enum


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class BatchStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchType(str, Enum):
    COMPLETION = "COMPLETION"
    EMBEDDING = "EMBEDDING"


class LabelStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class EmbeddingStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
