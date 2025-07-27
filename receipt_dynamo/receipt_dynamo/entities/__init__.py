"""
Entity classes for the receipt_dynamo package.

TODO: Make this import work with intellisense
"""

from receipt_dynamo.entities.ai_usage_metric import (  # noqa: F401
    AIUsageMetric,
    item_to_ai_usage_metric,
)
from receipt_dynamo.entities.batch_summary import BatchSummary  # noqa: F401
from receipt_dynamo.entities.batch_summary import item_to_batch_summary
from receipt_dynamo.entities.completion_batch_result import (  # noqa: F401
    CompletionBatchResult,
    item_to_completion_batch_result,
)
from receipt_dynamo.entities.embedding_batch_result import (  # noqa: F401
    EmbeddingBatchResult,
    item_to_embedding_batch_result,
)
from receipt_dynamo.entities.image import Image, item_to_image  # noqa: F401
from receipt_dynamo.entities.image_details import ImageDetails  # noqa: F401
from receipt_dynamo.entities.instance import Instance  # noqa: F401
from receipt_dynamo.entities.instance import item_to_instance
from receipt_dynamo.entities.instance_job import InstanceJob  # noqa: F401
from receipt_dynamo.entities.instance_job import item_to_instance_job
from receipt_dynamo.entities.job import item_to_job, Job  # noqa: F401
from receipt_dynamo.entities.job_checkpoint import JobCheckpoint  # noqa: F401
from receipt_dynamo.entities.job_checkpoint import item_to_job_checkpoint
from receipt_dynamo.entities.job_dependency import JobDependency  # noqa: F401
from receipt_dynamo.entities.job_dependency import item_to_job_dependency
from receipt_dynamo.entities.job_log import JobLog  # noqa: F401
from receipt_dynamo.entities.job_log import item_to_job_log
from receipt_dynamo.entities.job_metric import JobMetric  # noqa: F401
from receipt_dynamo.entities.job_metric import item_to_job_metric
from receipt_dynamo.entities.job_resource import JobResource  # noqa: F401
from receipt_dynamo.entities.job_resource import item_to_job_resource
from receipt_dynamo.entities.job_status import JobStatus  # noqa: F401
from receipt_dynamo.entities.job_status import item_to_job_status
from receipt_dynamo.entities.label_count_cache import (  # noqa: F401
    item_to_label_count_cache,
    LabelCountCache,
)
from receipt_dynamo.entities.label_hygiene_result import (  # noqa: F401
    item_to_label_hygiene_result,
    LabelHygieneResult,
)
from receipt_dynamo.entities.label_metadata import (  # noqa: F401
    item_to_label_metadata,
    LabelMetadata,
)
from receipt_dynamo.entities.letter import item_to_letter, Letter  # noqa: F401
from receipt_dynamo.entities.line import item_to_line, Line  # noqa: F401
from receipt_dynamo.entities.ocr_job import OCRJob  # noqa: F401
from receipt_dynamo.entities.ocr_job import item_to_ocr_job
from receipt_dynamo.entities.ocr_routing_decision import (  # noqa: F401
    item_to_ocr_routing_decision,
    OCRRoutingDecision,
)
from receipt_dynamo.entities.places_cache import PlacesCache  # noqa: F401
from receipt_dynamo.entities.places_cache import item_to_places_cache
from receipt_dynamo.entities.queue_job import QueueJob  # noqa: F401
from receipt_dynamo.entities.queue_job import item_to_queue_job
from receipt_dynamo.entities.receipt import Receipt  # noqa: F401
from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_analysis import (  # noqa: F401
    ReceiptAnalysis,
)
from receipt_dynamo.entities.receipt_chatgpt_validation import (  # noqa: F401
    item_to_receipt_chat_gpt_validation,
    ReceiptChatGPTValidation,
)
from receipt_dynamo.entities.receipt_details import (  # noqa: F401
    ReceiptDetails,
)
from receipt_dynamo.entities.receipt_field import ReceiptField  # noqa: F401
from receipt_dynamo.entities.receipt_field import item_to_receipt_field
from receipt_dynamo.entities.receipt_label_analysis import (  # noqa: F401
    item_to_receipt_label_analysis,
    ReceiptLabelAnalysis,
)
from receipt_dynamo.entities.receipt_letter import (  # noqa: F401
    item_to_receipt_letter,
    ReceiptLetter,
)
from receipt_dynamo.entities.receipt_line import ReceiptLine  # noqa: F401
from receipt_dynamo.entities.receipt_line import item_to_receipt_line
from receipt_dynamo.entities.receipt_line_item_analysis import (  # noqa: F401
    item_to_receipt_line_item_analysis,
    ReceiptLineItemAnalysis,
)
from receipt_dynamo.entities.receipt_metadata import (  # noqa: F401
    item_to_receipt_metadata,
    ReceiptMetadata,
)
from receipt_dynamo.entities.receipt_section import (  # noqa: F401
    item_to_receipt_section,
    ReceiptSection,
)

# Import receipt structure analysis types before __all__
from receipt_dynamo.entities.receipt_structure_analysis import (  # noqa: F401
    ContentPattern,
    item_to_receipt_structure_analysis,
    ReceiptStructureAnalysis,
    SpatialPattern,
)
from receipt_dynamo.entities.receipt_validation_category import (  # noqa: F401
    item_to_receipt_validation_category,
    ReceiptValidationCategory,
)
from receipt_dynamo.entities.receipt_validation_result import (  # noqa: F401
    item_to_receipt_validation_result,
    ReceiptValidationResult,
)
from receipt_dynamo.entities.receipt_validation_summary import (  # noqa: F401
    item_to_receipt_validation_summary,
    ReceiptValidationSummary,
)
from receipt_dynamo.entities.receipt_word import ReceiptWord  # noqa: F401
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (  # noqa: F401
    item_to_receipt_word_label,
    ReceiptWordLabel,
)
from receipt_dynamo.entities.rwl_queue import Queue  # noqa: F401
from receipt_dynamo.entities.rwl_queue import item_to_queue

# Re-export utility functions needed by other modules
from receipt_dynamo.entities.util import assert_valid_uuid  # noqa: F401
from receipt_dynamo.entities.word import item_to_word, Word  # noqa: F401

__all__ = [
    # Core entities
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
    # Utility functions
    "assert_valid_uuid",
    # Additional exports from receipt_structure_analysis
    "ContentPattern",
    "SpatialPattern",
]
