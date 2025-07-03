"""
Entity classes for the receipt_dynamo package.

TODO: Make this import work with intellisense
"""

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric  # noqa: F401
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
from receipt_dynamo.entities.job import Job, item_to_job  # noqa: F401
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
from receipt_dynamo.entities.ocr_job import (
    OCRJob,  # noqa: F401
    item_to_ocr_job,
)
from receipt_dynamo.entities.ocr_routing_decision import (  # noqa: F401
    OCRRoutingDecision,
    item_to_ocr_routing_decision,
)
from receipt_dynamo.entities.queue_job import QueueJob  # noqa: F401
from receipt_dynamo.entities.queue_job import item_to_queue_job
from receipt_dynamo.entities.receipt import Receipt  # noqa: F401
from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_analysis import (
    ReceiptAnalysis,
)  # noqa: F401
from receipt_dynamo.entities.receipt_chatgpt_validation import (  # noqa: F401
    ReceiptChatGPTValidation,
    item_to_receipt_chat_gpt_validation,
)
from receipt_dynamo.entities.receipt_details import (
    ReceiptDetails,
)  # noqa: F401
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
from receipt_dynamo.entities.receipt_metadata import (  # noqa: F401
    ReceiptMetadata,
    item_to_receipt_metadata,
)
from receipt_dynamo.entities.receipt_section import (  # noqa: F401
    ReceiptSection,
    item_to_receipt_section,
)
from receipt_dynamo.entities.receipt_structure_analysis import (  # noqa: F401
    ReceiptStructureAnalysis,
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
from receipt_dynamo.entities.rwl_queue import (
    Queue,  # noqa: F401
    item_to_queue,
)

# Re-export utility functions needed by other modules
from receipt_dynamo.entities.util import assert_valid_uuid  # noqa: F401
from receipt_dynamo.entities.word import Word, item_to_word  # noqa: F401
