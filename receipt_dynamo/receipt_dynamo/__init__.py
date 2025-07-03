"""DynamoDB utility package for receipt data."""

# mypy: ignore-errors

__version__ = "0.1.0"

# Define public API - this tells both users and linters what's intentionally exported
__all__ = [
    "Image",
    "Letter",
    "Line",
    "Receipt",
    "ReceiptLetter",
    "ReceiptLine",
    "ReceiptWord",
    "Word",
    "item_to_image",
    "item_to_letter",
    "item_to_line",
    "item_to_receipt",
    "item_to_receipt_letter",
    "item_to_receipt_line",
    "item_to_receipt_word",
    "item_to_word",
    "ReceiptWordLabel",
    "item_to_receipt_word_label",
    "ReceiptField",
    "item_to_receipt_field",
    "ReceiptLabelAnalysis",
    "item_to_receipt_label_analysis",
    "ReceiptStructureAnalysis",
    "item_to_receipt_structure_analysis",
    "ReceiptLineItemAnalysis",
    "item_to_receipt_line_item_analysis",
    "ReceiptAnalysis",
    "Job",
    "item_to_job",
    "JobMetric",
    "item_to_job_metric",
    "JobResource",
    "item_to_job_resource",
    "JobStatus",
    "item_to_job_status",
    "Instance",
    "item_to_instance",
    "InstanceJob",
    "item_to_instance_job",
    "DynamoClient",
    "export_image",
    "import_image",
    "JobService",
    "QueueService",
    "InstanceService",
    "ReceiptValidationSummary",
    "item_to_receipt_validation_summary",
    "ReceiptValidationResult",
    "item_to_receipt_validation_result",
    "ReceiptValidationCategory",
    "item_to_receipt_validation_category",
    "ReceiptChatGPTValidation",
    "AIUsageMetric",
    "item_to_ai_usage_metric",
    "ResilientDynamoClient",
    # Resilience patterns (moved from receipt_label)
    "BatchQueue",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "RetryManager",
    "retry_with_backoff",
]

# Entities must be imported first to avoid circular imports
from receipt_dynamo.entities import (
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptAnalysis,
    ReceiptChatGPTValidation,
    ReceiptField,
    ReceiptLabelAnalysis,
    ReceiptLetter,
    ReceiptLine,
    ReceiptLineItemAnalysis,
    ReceiptStructureAnalysis,
    ReceiptValidationCategory,
    ReceiptValidationResult,
    ReceiptValidationSummary,
    ReceiptWord,
    ReceiptWordLabel,
    Word,
    item_to_image,
    item_to_letter,
    item_to_line,
    item_to_receipt,
    item_to_receipt_chat_gpt_validation,
    item_to_receipt_field,
    item_to_receipt_label_analysis,
    item_to_receipt_letter,
    item_to_receipt_line,
    item_to_receipt_line_item_analysis,
    item_to_receipt_structure_analysis,
    item_to_receipt_validation_category,
    item_to_receipt_validation_result,
    item_to_receipt_validation_summary,
    item_to_receipt_word,
    item_to_receipt_word_label,
    item_to_word,
)
from receipt_dynamo.entities.ai_usage_metric import (
    AIUsageMetric,
    item_to_ai_usage_metric,
)
from receipt_dynamo.entities.instance import Instance, item_to_instance
from receipt_dynamo.entities.instance_job import (
    InstanceJob,
    item_to_instance_job,
)
from receipt_dynamo.entities.job import Job, item_to_job
from receipt_dynamo.entities.job_metric import JobMetric, item_to_job_metric
from receipt_dynamo.entities.job_resource import (
    JobResource,
    item_to_job_resource,
)
from receipt_dynamo.entities.job_status import JobStatus, item_to_job_status
from receipt_dynamo.entities.receipt_structure_analysis import (
    ContentPattern,
    ReceiptSection,
    SpatialPattern,
)

# Only import what's actually used elsewhere in the package
try:  # Optional dependency
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo.data.resilient_dynamo_client import (
        ResilientDynamoClient,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    class DynamoClient:  # type: ignore
        """Placeholder for DynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError(
                "boto3 is required for DynamoClient"
            ) from exc

    class ResilientDynamoClient:  # type: ignore
        """Placeholder for ResilientDynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError(
                "boto3 is required for ResilientDynamoClient"
            ) from exc


try:  # Optional dependency
    from receipt_dynamo.data.export_image import export_image
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    def export_image(*_, **__):
        raise ModuleNotFoundError(
            "boto3 is required for export_image"
        ) from exc


try:
    from receipt_dynamo.data.import_image import import_image
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    def import_image(*_, **__):
        raise ModuleNotFoundError(
            "boto3 is required for import_image"
        ) from exc


try:  # Optional dependency
    from receipt_dynamo.services.instance_service import InstanceService
    from receipt_dynamo.services.job_service import JobService
    from receipt_dynamo.services.queue_service import QueueService
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    class _ServicePlaceholder:  # type: ignore
        def __init__(self, *_, **__):
            raise ModuleNotFoundError(
                "boto3 is required for service classes"
            ) from exc

    InstanceService = _ServicePlaceholder
    JobService = _ServicePlaceholder
    QueueService = _ServicePlaceholder

# Import resilience patterns
from receipt_dynamo.utils import (
    BatchQueue,
    CircuitBreaker,
    CircuitBreakerOpenError,
    RetryManager,
    retry_with_backoff,
)

# For backward compatibility:
