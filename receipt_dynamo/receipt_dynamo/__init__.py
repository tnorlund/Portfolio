"""
DynamoDB utility package for receipt data.
"""

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
    "process",
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
]

# Entities must be imported first to avoid circular imports
from receipt_dynamo.entities import (
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
    itemToImage,
    itemToLetter,
    itemToLine,
    itemToReceipt,
    itemToReceiptLetter,
    itemToReceiptLine,
    itemToReceiptWord,
    itemToWord,
    ReceiptWordLabel,
    itemToReceiptWordLabel,
    ReceiptField,
    itemToReceiptField,
    ReceiptLabelAnalysis,
    itemToReceiptLabelAnalysis,
    ReceiptStructureAnalysis,
    itemToReceiptStructureAnalysis,
    ReceiptLineItemAnalysis,
    itemToReceiptLineItemAnalysis,
    ReceiptValidationSummary,
    itemToReceiptValidationSummary,
    ReceiptValidationResult,
    itemToReceiptValidationResult,
    ReceiptValidationCategory,
    itemToReceiptValidationCategory,
    ReceiptChatGPTValidation,
    itemToReceiptChatGPTValidation,
    ReceiptAnalysis,
)
from receipt_dynamo.entities.receipt_word_tag import (
    ReceiptWordTag,
    itemToReceiptWordTag,
)
from receipt_dynamo.entities.word_tag import WordTag, itemToWordTag
from receipt_dynamo.entities.job import Job, itemToJob
from receipt_dynamo.entities.job_metric import JobMetric, itemToJobMetric
from receipt_dynamo.entities.job_resource import JobResource, itemToJobResource
from receipt_dynamo.entities.job_status import JobStatus, itemToJobStatus
from receipt_dynamo.entities.instance import Instance, itemToInstance
from receipt_dynamo.entities.instance_job import InstanceJob, itemToInstanceJob
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptStructureAnalysis,
    itemToReceiptStructureAnalysis,
    SpatialPattern,
    ContentPattern,
    ReceiptSection,
)

# Only import what's actually used elsewhere in the package
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.process import process
from receipt_dynamo.data.export_image import export_image
from receipt_dynamo.data.import_image import import_image

# Service layer imports
from receipt_dynamo.services.job_service import JobService
from receipt_dynamo.services.queue_service import QueueService
from receipt_dynamo.services.instance_service import InstanceService

# For backward compatibility:
