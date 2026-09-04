"""
Entity classes for the receipt_dynamo package.

TODO: Make this import work with intellisense
"""

# =============================================================================
# Entity classes
# =============================================================================
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
from receipt_dynamo.entities.coreml_export_job import (  # noqa: F401
    CoreMLExportJob,
    item_to_coreml_export_job,
)
from receipt_dynamo.entities.identifier_mixins import (
    ImageIdentifierMixin,
    ImageLineIdentifierMixin,
    ImageWordIdentifierMixin,
    JobIdentifierMixin,
    LineIdentifierMixin,
    ReceiptIdentifierMixin,
    WordIdentifierMixin,
)
from receipt_dynamo.entities.image import Image, item_to_image  # noqa: F401
from receipt_dynamo.entities.image_details import ImageDetails  # noqa: F401
from receipt_dynamo.entities.job import Job, item_to_job  # noqa: F401
from receipt_dynamo.entities.job_log import JobLog  # noqa: F401
from receipt_dynamo.entities.job_log import item_to_job_log
from receipt_dynamo.entities.job_metric import JobMetric  # noqa: F401
from receipt_dynamo.entities.job_metric import item_to_job_metric
from receipt_dynamo.entities.label_count_cache import (  # noqa: F401
    LabelCountCache,
    item_to_label_count_cache,
)
from receipt_dynamo.entities.letter import Letter, item_to_letter  # noqa: F401
from receipt_dynamo.entities.line import Line, item_to_line  # noqa: F401
from receipt_dynamo.entities.merchant_catalog_item import (  # noqa: F401
    MerchantCatalogItem,
    item_to_merchant_catalog_item,
)
from receipt_dynamo.entities.merchant_font import (  # noqa: F401
    MerchantFont,
    item_to_merchant_font,
)
from receipt_dynamo.entities.merchant_truth import (  # noqa: F401
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthAudit,
    MerchantTruthComponent,
    MerchantTruthManifest,
    MerchantTruthProposal,
    canonical_json_bytes,
    compute_bundle_hash,
    hash_payload,
    merchant_truth_pk,
    version_prefix,
)
from receipt_dynamo.entities.merchant_truth_gate import (  # noqa: F401
    GATE_OVERALL_VERDICTS,
    MerchantTruthGateRecord,
    gate_version_segment,
)
from receipt_dynamo.entities.ocr_job import OCRJob  # noqa: F401
from receipt_dynamo.entities.ocr_job import item_to_ocr_job
from receipt_dynamo.entities.ocr_routing_decision import (  # noqa: F401
    OCRRoutingDecision,
    item_to_ocr_routing_decision,
)
from receipt_dynamo.entities.places_cache import PlacesCache  # noqa: F401
from receipt_dynamo.entities.places_cache import item_to_places_cache
from receipt_dynamo.entities.receipt import Receipt  # noqa: F401
from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_barcode import (  # noqa: F401
    ReceiptBarcode,
    item_to_receipt_barcode,
)
from receipt_dynamo.entities.receipt_bundle import (
    ReceiptBundle,
    ReceiptBundlePage,
)
from receipt_dynamo.entities.receipt_chatgpt_validation import (  # noqa: F401
    ReceiptChatGPTValidation,
    item_to_receipt_chat_gpt_validation,
)
from receipt_dynamo.entities.receipt_details import (  # noqa: F401
    ReceiptDetails,
)
from receipt_dynamo.entities.receipt_embedding import (
    EMBEDDING_DIMENSIONS,
    ReceiptEmbedding,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
    item_to_receipt_embedding,
    item_to_receipt_line_embedding,
    item_to_receipt_word_embedding,
)
from receipt_dynamo.entities.receipt_field import ReceiptField  # noqa: F401
from receipt_dynamo.entities.receipt_field import item_to_receipt_field
from receipt_dynamo.entities.receipt_letter import (  # noqa: F401
    ReceiptLetter,
    item_to_receipt_letter,
)
from receipt_dynamo.entities.receipt_line import ReceiptLine  # noqa: F401
from receipt_dynamo.entities.receipt_line import item_to_receipt_line
from receipt_dynamo.entities.receipt_line_item import (
    ReceiptLineItem,
    item_to_receipt_line_item,
)
from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    item_to_receipt_metadata,
)
from receipt_dynamo.entities.receipt_place import (
    ReceiptPlace,
    item_to_receipt_place,
)
from receipt_dynamo.entities.receipt_row import (
    ReceiptRow,
    item_to_receipt_row,
)
from receipt_dynamo.entities.receipt_section import (
    ReceiptSection,
    item_to_receipt_section,
    validate_section_row_coverage,
)
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
    extract_amount,
    parse_date,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
    item_to_receipt_summary_record,
)
from receipt_dynamo.entities.receipt_text_geometry_entity import (
    ReceiptTextGeometryEntity,
)
from receipt_dynamo.entities.receipt_validation_category import (  # noqa: F401
    ReceiptValidationCategory,
    item_to_receipt_validation_category,
)
from receipt_dynamo.entities.receipt_word import ReceiptWord  # noqa: F401
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (  # noqa: F401
    ReceiptWordLabel,
    item_to_receipt_word_label,
)

# =============================================================================
# Base classes and mixins for entity consolidation
# =============================================================================
from receipt_dynamo.entities.text_geometry_entity import (
    GeometryEntity,  # Backwards compatibility alias
)
from receipt_dynamo.entities.text_geometry_entity import (
    TextGeometryEntity,
)

# Re-export utility functions needed by other modules
from receipt_dynamo.entities.util import assert_valid_uuid  # noqa: F401
from receipt_dynamo.entities.value_objects import (
    Angle,
    BoundingBox,
    CDNVariants,
    Corners,
    Point,
    S3Location,
)
from receipt_dynamo.entities.word import Word, item_to_word  # noqa: F401

__all__ = [
    # Base classes and mixins
    "TextGeometryEntity",
    "ReceiptTextGeometryEntity",
    "GeometryEntity",  # Backwards compatibility alias
    "ImageIdentifierMixin",
    "ImageLineIdentifierMixin",
    "ImageWordIdentifierMixin",
    "JobIdentifierMixin",
    "LineIdentifierMixin",
    "ReceiptIdentifierMixin",
    "WordIdentifierMixin",
    # Value objects
    "Angle",
    "BoundingBox",
    "CDNVariants",
    "Corners",
    "Point",
    "S3Location",
    # Core entities
    "AIUsageMetric",
    "BatchSummary",
    "CompactionLock",
    "CompactionRun",
    "CoreMLExportJob",
    "Image",
    "ImageDetails",
    "Job",
    "JobLog",
    "JobMetric",
    "LabelCountCache",
    "Letter",
    "Line",
    "MerchantCatalogItem",
    "MerchantFont",
    "MerchantTruthActive",
    "MerchantTruthAudit",
    "MerchantTruthComponent",
    "MerchantTruthGateRecord",
    "MerchantTruthManifest",
    "MerchantTruthProposal",
    "OCRJob",
    "OCRRoutingDecision",
    "PlacesCache",
    "Receipt",
    "ReceiptBarcode",
    "ReceiptChatGPTValidation",
    "ReceiptBundle",
    "ReceiptBundlePage",
    "MonetaryTotals",
    "ReceiptSummary",
    "ReceiptSummaryRecord",
    "ReceiptDetails",
    "ReceiptEmbedding",
    "ReceiptField",
    "ReceiptLetter",
    "ReceiptLine",
    "ReceiptLineEmbedding",
    "ReceiptLineItem",
    "ReceiptMetadata",
    "ReceiptPlace",
    "ReceiptRow",
    "ReceiptSection",
    "ReceiptValidationCategory",
    "ReceiptWord",
    "ReceiptWordEmbedding",
    "ReceiptWordLabel",
    "Word",
    # Item conversion functions
    "item_to_ai_usage_metric",
    "item_to_batch_summary",
    "item_to_compaction_lock",
    "item_to_compaction_run",
    "item_to_coreml_export_job",
    "item_to_image",
    "item_to_job",
    "item_to_job_log",
    "item_to_job_metric",
    "item_to_label_count_cache",
    "item_to_letter",
    "item_to_line",
    "item_to_merchant_catalog_item",
    "item_to_merchant_font",
    "item_to_ocr_job",
    "item_to_ocr_routing_decision",
    "item_to_places_cache",
    "item_to_receipt",
    "item_to_receipt_barcode",
    "item_to_receipt_chat_gpt_validation",
    "item_to_receipt_summary_record",
    "item_to_receipt_field",
    "item_to_receipt_embedding",
    "item_to_receipt_letter",
    "item_to_receipt_line",
    "item_to_receipt_line_embedding",
    "item_to_receipt_line_item",
    "item_to_receipt_metadata",
    "item_to_receipt_place",
    "item_to_receipt_row",
    "item_to_receipt_section",
    "validate_section_row_coverage",
    "item_to_receipt_validation_category",
    "item_to_receipt_word",
    "item_to_receipt_word_embedding",
    "item_to_receipt_word_label",
    "item_to_word",
    # Utility functions
    "assert_valid_uuid",
    "EMBEDDING_DIMENSIONS",
    "extract_amount",
    "parse_date",
    "COMPONENT_NAMES",
    "canonical_json_bytes",
    "compute_bundle_hash",
    "hash_payload",
    "merchant_truth_pk",
    "version_prefix",
]
