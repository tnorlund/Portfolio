"""DynamoDB utility package for receipt data."""

# mypy: ignore-errors

__version__ = "0.2.0"

# Import all entities
from receipt_dynamo.entities import *  # noqa: F401, F403

# Additional exports that might not be in entities.__all__
from receipt_dynamo.entities import (
    ContentPattern,
    ReceiptSection,
    SpatialPattern,
)

# Import services - requires boto3
try:
    from receipt_dynamo.services import *  # noqa: F401, F403
except ModuleNotFoundError:
    # Fallback placeholders when boto3 is not available
    class _ServicePlaceholder:  # type: ignore
        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for service classes")

    InstanceService = _ServicePlaceholder
    JobService = _ServicePlaceholder
    QueueService = _ServicePlaceholder

# Import DynamoDB clients - requires boto3
try:
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo.data.resilient_dynamo_client import (
        ResilientDynamoClient,
    )
except ModuleNotFoundError:
    # Placeholders when boto3 is unavailable
    class DynamoClient:  # type: ignore
        """Placeholder for DynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for DynamoClient")

    class ResilientDynamoClient:  # type: ignore
        """Placeholder for ResilientDynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError(
                "boto3 is required for ResilientDynamoClient"
            )


# Import data operations - requires boto3
try:
    from receipt_dynamo.data.export_image import export_image
except ModuleNotFoundError:

    def export_image(*_, **__):
        raise ModuleNotFoundError("boto3 is required for export_image")


try:
    from receipt_dynamo.data.import_image import import_image
except ModuleNotFoundError:

    def import_image(*_, **__):
        raise ModuleNotFoundError("boto3 is required for import_image")


# Import resilience patterns
from receipt_dynamo.utils import *  # noqa: F401, F403

# Define public API explicitly
__all__ = [
    # Version
    "__version__",
    # All entities (from entities.__all__)
    "AIUsageMetric",
    "BatchSummary",
    "CompletionBatchResult",
    "EmbeddingBatchResult",
    "Image",
    "ImageDetails",
    "Instance",
    "InstanceJob",
    "Job",
    "JobCheckpoint",
    "JobDependency",
    "JobLog",
    "JobMetric",
    "JobResource",
    "JobStatus",
    "LabelCountCache",
    "LabelHygieneResult",
    "LabelMetadata",
    "Letter",
    "Line",
    "OCRJob",
    "OCRRoutingDecision",
    "PlacesCache",
    "Queue",
    "QueueJob",
    "Receipt",
    "ReceiptAnalysis",
    "ReceiptChatGPTValidation",
    "ReceiptDetails",
    "ReceiptField",
    "ReceiptLabelAnalysis",
    "ReceiptLetter",
    "ReceiptLine",
    "ReceiptLineItemAnalysis",
    "ReceiptMetadata",
    "ReceiptSection",
    "ReceiptStructureAnalysis",
    "ReceiptValidationCategory",
    "ReceiptValidationResult",
    "ReceiptValidationSummary",
    "ReceiptWord",
    "ReceiptWordLabel",
    "Word",
    # Item conversion functions
    "item_to_ai_usage_metric",
    "item_to_batch_summary",
    "item_to_completion_batch_result",
    "item_to_embedding_batch_result",
    "item_to_image",
    "item_to_instance",
    "item_to_instance_job",
    "item_to_job",
    "item_to_job_checkpoint",
    "item_to_job_dependency",
    "item_to_job_log",
    "item_to_job_metric",
    "item_to_job_resource",
    "item_to_job_status",
    "item_to_label_count_cache",
    "item_to_label_hygiene_result",
    "item_to_label_metadata",
    "item_to_letter",
    "item_to_line",
    "item_to_ocr_job",
    "item_to_ocr_routing_decision",
    "item_to_places_cache",
    "item_to_queue",
    "item_to_queue_job",
    "item_to_receipt",
    "item_to_receipt_chat_gpt_validation",
    "item_to_receipt_field",
    "item_to_receipt_label_analysis",
    "item_to_receipt_letter",
    "item_to_receipt_line",
    "item_to_receipt_line_item_analysis",
    "item_to_receipt_metadata",
    "item_to_receipt_section",
    "item_to_receipt_structure_analysis",
    "item_to_receipt_validation_category",
    "item_to_receipt_validation_result",
    "item_to_receipt_validation_summary",
    "item_to_receipt_word",
    "item_to_receipt_word_label",
    "item_to_word",
    # Services (from services.__all__)
    "InstanceService",
    "JobService",
    "QueueService",
    # DynamoDB clients
    "DynamoClient",
    "ResilientDynamoClient",
    # Data operations
    "export_image",
    "import_image",
    # Resilience patterns (from utils.__all__)
    "BatchQueue",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "RetryManager",
    "retry_with_backoff",
    # Additional exports
    "ContentPattern",
    "SpatialPattern",
    "assert_valid_uuid",
]
