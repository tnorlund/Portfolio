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
from receipt_dynamo.entities.compaction_lock import (  # noqa: F401
    CompactionLock,
    item_to_compaction_lock,
)
from receipt_dynamo.entities.compaction_run import (  # noqa: F401
    CompactionRun,
    item_to_compaction_run,
)
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
from receipt_dynamo.entities.job import Job, item_to_job  # noqa: F401
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
    LabelCountCache,
    item_to_label_count_cache,
)
from receipt_dynamo.entities.label_hygiene_result import (  # noqa: F401
    LabelHygieneResult,
    item_to_label_hygiene_result,
)
from receipt_dynamo.entities.label_metadata import (  # noqa: F401
    LabelMetadata,
    item_to_label_metadata,
)
from receipt_dynamo.entities.letter import Letter, item_to_letter  # noqa: F401
from receipt_dynamo.entities.line import Line, item_to_line  # noqa: F401
from receipt_dynamo.entities.ocr_job import OCRJob  # noqa: F401
from receipt_dynamo.entities.ocr_job import item_to_ocr_job
from receipt_dynamo.entities.ocr_routing_decision import (  # noqa: F401
    OCRRoutingDecision,
    item_to_ocr_routing_decision,
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
    ReceiptChatGPTValidation,
    item_to_receipt_chat_gpt_validation,
)
from receipt_dynamo.entities.receipt_details import (  # noqa: F401
    ReceiptDetails,
)
from receipt_dynamo.entities.receipt_field import ReceiptField  # noqa: F401
from receipt_dynamo.entities.receipt_field import item_to_receipt_field
from receipt_dynamo.entities.receipt_label_analysis import (  # noqa: F401
    ReceiptLabelAnalysis,
    item_to_receipt_label_analysis,
)
from receipt_dynamo.entities.receipt_letter import (  # noqa: F401
    ReceiptLetter,
    item_to_receipt_letter,
)
from receipt_dynamo.entities.receipt_line import ReceiptLine  # noqa: F401
from receipt_dynamo.entities.receipt_line import item_to_receipt_line
from receipt_dynamo.entities.receipt_line_item_analysis import (  # noqa: F401
    ReceiptLineItemAnalysis,
    item_to_receipt_line_item_analysis,
)
from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    item_to_receipt_metadata,
)
from receipt_dynamo.entities.place_cluster import (
    ClusterType,
    PlaceCluster,
)
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_section import (
    ReceiptSection,
    item_to_receipt_section,
)

# Import receipt structure analysis types before __all__
from receipt_dynamo.entities.receipt_structure_analysis import (
    ContentPattern,
    ReceiptStructureAnalysis,
    SpatialPattern,
    item_to_receipt_structure_analysis,
)
from receipt_dynamo.entities.receipt_validation_category import (  # noqa: F401
    ReceiptValidationCategory,
    item_to_receipt_validation_category,
)
from receipt_dynamo.entities.receipt_validation_result import (  # noqa: F401
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)
from receipt_dynamo.entities.receipt_validation_summary import (  # noqa: F401
    ReceiptValidationSummary,
    item_to_receipt_validation_summary,
)
from receipt_dynamo.entities.receipt_word import ReceiptWord  # noqa: F401
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (  # noqa: F401
    ReceiptWordLabel,
    item_to_receipt_word_label,
)
from receipt_dynamo.entities.receipt_word_label_spatial_analysis import (  # noqa: F401
    ReceiptWordLabelSpatialAnalysis,
    SpatialRelationship,
    item_to_receipt_word_label_spatial_analysis,
)
from receipt_dynamo.entities.rwl_queue import Queue  # noqa: F401
from receipt_dynamo.entities.rwl_queue import item_to_queue

# Re-export utility functions needed by other modules
from receipt_dynamo.entities.util import assert_valid_uuid  # noqa: F401
from receipt_dynamo.entities.word import Word, item_to_word  # noqa: F401

__all__ = [
    # Core entities
    "AIUsageMetric",
    "BatchSummary",
    "CompactionLock",
    "CompactionRun",
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
    "PlaceCluster",
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
    "ReceiptPlace",
    "ReceiptSection",
    "ReceiptStructureAnalysis",
    "ReceiptValidationCategory",
    "ReceiptValidationResult",
    "ReceiptValidationSummary",
    "ReceiptWord",
    "ReceiptWordLabel",
    "ReceiptWordLabelSpatialAnalysis",
    "SpatialRelationship",
    "Word",
    # Item conversion functions
    "item_to_ai_usage_metric",
    "item_to_batch_summary",
    "item_to_compaction_lock",
    "item_to_completion_batch_result",
    "item_to_compaction_run",
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
    "item_to_receipt_word_label_spatial_analysis",
    "item_to_word",
    # Utility functions
    "assert_valid_uuid",
    # Additional exports from receipt_structure_analysis
    "ContentPattern",
    "SpatialPattern",
    # Cluster types
    "ClusterType",
]
