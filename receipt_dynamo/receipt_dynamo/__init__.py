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
    "itemToImage",
    "itemToLetter",
    "itemToLine",
    "itemToReceipt",
    "itemToReceiptLetter",
    "itemToReceiptLine",
    "itemToReceiptWord",
    "itemToWord",
    "ReceiptWordTag",
    "itemToReceiptWordTag",
    "ReceiptWordLabel",
    "itemToReceiptWordLabel",
    "ReceiptField",
    "itemToReceiptField",
    "ReceiptLabelAnalysis",
    "itemToReceiptLabelAnalysis",
    "ReceiptStructureAnalysis",
    "itemToReceiptStructureAnalysis",
    "ReceiptLineItemAnalysis",
    "itemToReceiptLineItemAnalysis",
    "ReceiptAnalysis",
    "WordTag",
    "itemToWordTag",
    "Job",
    "itemToJob",
    "JobMetric",
    "itemToJobMetric",
    "JobResource",
    "itemToJobResource",
    "JobStatus",
    "itemToJobStatus",
    "Instance",
    "itemToInstance",
    "InstanceJob",
    "itemToInstanceJob",
    "DynamoClient",
    "export_image",
    "import_image",
    "JobService",
    "QueueService",
    "InstanceService",
    "ReceiptValidationSummary",
    "itemToReceiptValidationSummary",
    "ReceiptValidationResult",
    "itemToReceiptValidationResult",
    "ReceiptValidationCategory",
    "itemToReceiptValidationCategory",
    "ReceiptChatGPTValidation",
    "AIUsageMetric",
    "itemToAIUsageMetric",
    "ResilientDynamoClient",
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
    itemToImage,
    itemToLetter,
    itemToLine,
    itemToReceipt,
    itemToReceiptChatGPTValidation,
    itemToReceiptField,
    itemToReceiptLabelAnalysis,
    itemToReceiptLetter,
    itemToReceiptLine,
    itemToReceiptLineItemAnalysis,
    itemToReceiptStructureAnalysis,
    itemToReceiptValidationCategory,
    itemToReceiptValidationResult,
    itemToReceiptValidationSummary,
    itemToReceiptWord,
    itemToReceiptWordLabel,
    itemToWord,
)
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric, itemToAIUsageMetric
from receipt_dynamo.entities.instance import Instance, itemToInstance
from receipt_dynamo.entities.instance_job import InstanceJob, itemToInstanceJob
from receipt_dynamo.entities.job import Job, itemToJob
from receipt_dynamo.entities.job_metric import JobMetric, itemToJobMetric
from receipt_dynamo.entities.job_resource import JobResource, itemToJobResource
from receipt_dynamo.entities.job_status import JobStatus, itemToJobStatus
from receipt_dynamo.entities.receipt_structure_analysis import (
    ContentPattern,
    ReceiptSection,
    SpatialPattern,
)
from receipt_dynamo.entities.receipt_word_tag import (
    ReceiptWordTag,
    itemToReceiptWordTag,
)
from receipt_dynamo.entities.word_tag import WordTag, itemToWordTag

# Only import what's actually used elsewhere in the package
try:  # Optional dependency
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo.data.resilient_dynamo_client import ResilientDynamoClient
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    class DynamoClient:  # type: ignore
        """Placeholder for DynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for DynamoClient") from exc

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
        raise ModuleNotFoundError("boto3 is required for export_image") from exc


try:
    from receipt_dynamo.data.import_image import import_image
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    def import_image(*_, **__):
        raise ModuleNotFoundError("boto3 is required for import_image") from exc


try:  # Optional dependency
    from receipt_dynamo.services.instance_service import InstanceService
    from receipt_dynamo.services.job_service import JobService
    from receipt_dynamo.services.queue_service import QueueService
except ModuleNotFoundError as exc:  # pragma: no cover - boto3 missing

    class _ServicePlaceholder:  # type: ignore
        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for service classes") from exc

    InstanceService = _ServicePlaceholder
    JobService = _ServicePlaceholder
    QueueService = _ServicePlaceholder

# For backward compatibility:
