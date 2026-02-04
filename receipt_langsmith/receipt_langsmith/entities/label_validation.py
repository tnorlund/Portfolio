"""Label validation trace schemas for receipt-label-validation project.

This module defines schemas for parsing trace inputs/outputs from the
receipt-label-validation LangSmith project, including child traces like
label_validation_chroma, label_validation_llm, llm_batch_validation,
s3_download_*_snapshot, openai_embed_*, and merchant_resolution_* traces.
"""

from typing import Optional

from pydantic import BaseModel, Field

# ----- Nested/Shared Models -----


class SimilarWordTrace(BaseModel):
    """A similar word found in ChromaDB vector search."""

    word_text: str = ""
    """Text of the similar word."""

    label: str = ""
    """Label of the similar word."""

    similarity: float = 0.0
    """Cosine similarity score (0-1)."""

    merchant_name: Optional[str] = None
    """Merchant where this word was found."""

    is_same_merchant: bool = False
    """Whether this word is from the same merchant."""


class SimilarityMatchTrace(BaseModel):
    """A similarity match for merchant resolution."""

    image_id: str = ""
    """Source image ID."""

    receipt_id: int = 0
    """Source receipt ID."""

    merchant_name: Optional[str] = None
    """Matched merchant name."""

    embedding_similarity: float = 0.0
    """Embedding similarity score (0-1)."""

    metadata_boost: float = 0.0
    """Confidence boost from metadata matching."""

    total_confidence: float = 0.0
    """Combined similarity + boost score."""


# ----- S3 Download Traces -----


class S3DownloadSnapshotInputs(BaseModel):
    """Inputs to s3_download_lines_snapshot and s3_download_words_snapshot."""

    bucket: str = ""
    """S3 bucket name."""

    collection: str = ""
    """Collection name: 'lines' or 'words'."""


class S3DownloadSnapshotOutputs(BaseModel):
    """Outputs from s3_download_lines_snapshot and
    s3_download_words_snapshot."""

    local_path: str = ""
    """Local file path where snapshot was downloaded."""

    status: str = ""
    """Download status: 'success', 'error', etc."""

    version_id: Optional[str] = None
    """S3 version ID of the downloaded snapshot."""


# ----- Embedding Traces -----


class OpenAIEmbedInputs(BaseModel):
    """Inputs to openai_embed_lines and openai_embed_words traces."""

    count: int = 0
    """Number of items to embed (line_count or word_count)."""

    model: str = ""
    """OpenAI model used (e.g., 'text-embedding-3-small')."""


class OpenAIEmbedOutputs(BaseModel):
    """Outputs from openai_embed_lines and openai_embed_words traces.

    Note: The actual embeddings are returned as List[List[float]] but
    we don't store them in the trace output - only metadata is captured.
    """

    embeddings_count: int = 0
    """Number of embeddings generated."""


# ----- ChromaDB Upsert Trace -----


class ChromaDBUpsertOutputs(BaseModel):
    """Outputs from chromadb_upsert trace."""

    lines_upserted: int = 0
    """Number of line embeddings upserted."""

    words_upserted: int = 0
    """Number of word embeddings upserted."""


# ----- Label Validation Traces -----


class LabelValidationInputs(BaseModel):
    """Inputs to label_validation_chroma and label_validation_llm traces."""

    image_id: str = ""
    """Receipt image ID (UUID)."""

    receipt_id: int = 0
    """Receipt ID within image (1-indexed)."""

    line_id: int = 0
    """Line ID containing the word."""

    word_id: int = 0
    """Word ID within the receipt."""

    word_text: str = ""
    """Text content of the word."""

    predicted_label: str = ""
    """Label predicted by the model."""


class LabelValidationOutputs(BaseModel):
    """Outputs from label_validation_chroma and label_validation_llm traces.

    These traces occur N times per receipt (once per word being validated).
    """

    image_id: str = ""
    """Receipt image ID (UUID)."""

    receipt_id: int = 0
    """Receipt ID within image (1-indexed)."""

    line_id: int = 0
    """Line ID containing the word."""

    word_id: int = 0
    """Word ID within the receipt."""

    word_text: str = ""
    """Text content of the word."""

    predicted_label: str = ""
    """Label predicted by the model."""

    final_label: str = ""
    """Final label after validation."""

    validation_source: str = ""
    """Source of validation: 'chroma' or 'llm'."""

    decision: str = ""
    """Decision: 'valid', 'invalid', or 'needs_review'."""

    confidence: float = 0.0
    """Confidence score (0-1)."""

    reasoning: str = ""
    """Explanation for the decision."""

    similar_words: list[SimilarWordTrace] = Field(default_factory=list)
    """Similar words found in vector search (Chroma validation only)."""

    merchant_name: Optional[str] = None
    """Merchant name for context."""


# ----- LLM Batch Validation -----


class LLMBatchValidationInputs(BaseModel):
    """Inputs to llm_batch_validation trace."""

    image_id: str = ""
    """Receipt image ID."""

    receipt_id: int = 0
    """Receipt ID within image."""

    word_count: int = 0
    """Number of words to validate in batch."""


class LLMBatchValidationOutputs(BaseModel):
    """Outputs from llm_batch_validation trace."""

    prompt: str = ""
    """Full prompt sent to LLM."""

    response: str = ""
    """Raw LLM response text."""

    label_count: int = 0
    """Number of labels validated."""

    merchant: Optional[str] = None
    """Detected merchant name."""


# ----- Merchant Resolution Traces -----


class MerchantResolutionInputs(BaseModel):
    """Inputs to merchant_resolution_chroma_* traces."""

    image_id: str = ""
    """Receipt image ID."""

    receipt_id: int = 0
    """Receipt ID within image."""

    resolution_tier: str = ""
    """Resolution method: 'phone', 'address', or 'text'."""


class MerchantResolutionOutputs(BaseModel):
    """Outputs from merchant_resolution_chroma_* traces.

    Traces include: merchant_resolution_chroma_phone,
    merchant_resolution_chroma_address, merchant_resolution_chroma_text.
    """

    image_id: str = ""
    """Receipt image ID."""

    receipt_id: int = 0
    """Receipt ID within image."""

    resolution_tier: str = ""
    """Resolution method: 'phone', 'address', or 'text'."""

    merchant_name: Optional[str] = None
    """Resolved merchant name."""

    place_id: Optional[str] = None
    """Google Place ID if found."""

    confidence: float = 0.0
    """Confidence in resolution (0-1)."""

    phone_extracted: Optional[str] = None
    """Extracted phone number (phone tier only)."""

    address_extracted: Optional[str] = None
    """Extracted address (address tier only)."""

    similarity_matches: list[SimilarityMatchTrace] = Field(
        default_factory=list
    )
    """Similarity matches from ChromaDB."""

    source_receipt: Optional[str] = None
    """Source receipt for cross-reference ('image_id#receipt_id' format)."""

    found: bool = False
    """Whether merchant was resolved."""


# ----- Root Trace -----


class ReceiptProcessingInputs(BaseModel):
    """Inputs to receipt_processing root trace."""

    image_id: str = ""
    """Receipt image ID (UUID)."""

    receipt_id: int = 0
    """Receipt ID within image (1-indexed)."""

    skip_llm: bool = False
    """If True, skip LLM validation and use Chroma only."""


class ReceiptProcessingOutputs(BaseModel):
    """Outputs from receipt_processing root trace.

    Aggregates results from all child traces including S3 downloads,
    embedding generation, label validation, and merchant resolution.
    """

    success: bool = False
    """Whether processing completed successfully."""

    run_id: str = ""
    """Unique run identifier (UUID for compaction run)."""

    lines_count: int = 0
    """Number of lines in receipt."""

    words_count: int = 0
    """Number of words in receipt."""

    merchant_found: bool = False
    """Whether merchant was resolved."""

    merchant_name: Optional[str] = None
    """Resolved merchant name."""

    merchant_place_id: Optional[str] = None
    """Google Place ID for merchant."""

    merchant_resolution_tier: Optional[str] = None
    """Method used for resolution: 'phone', 'address', or 'text'."""

    merchant_confidence: float = 0.0
    """Confidence in merchant resolution (0-1)."""

    labels_validated: int = 0
    """Total labels validated."""

    labels_corrected: int = 0
    """Number of labels corrected (invalidated predictions)."""

    chroma_validated: int = 0
    """Labels validated via ChromaDB consensus."""

    llm_validated: Optional[int] = None
    """Labels validated via LLM (None if skipped)."""

    error: Optional[str] = None
    """Error message if success is False."""


# ----- Aggregated Analytics Models -----


class LabelValidationSummary(BaseModel):
    """Summary of label validation for a receipt."""

    total_words: int = 0
    """Total words processed."""

    valid_count: int = 0
    """Labels marked valid (no change needed)."""

    invalid_count: int = 0
    """Labels that were invalidated and corrected."""

    needs_review_count: int = 0
    """Labels needing manual review."""

    chroma_count: int = 0
    """Validations done via ChromaDB."""

    llm_count: int = 0
    """Validations done via LLM."""

    avg_confidence: float = 0.0
    """Average confidence score."""


class MerchantResolutionSummary(BaseModel):
    """Summary of merchant resolution attempts."""

    phone_attempted: bool = False
    """Whether phone resolution was attempted."""

    phone_success: bool = False
    """Whether phone resolution succeeded."""

    address_attempted: bool = False
    """Whether address resolution was attempted."""

    address_success: bool = False
    """Whether address resolution succeeded."""

    text_attempted: bool = False
    """Whether text resolution was attempted."""

    text_success: bool = False
    """Whether text resolution succeeded."""

    final_tier: Optional[str] = None
    """Tier that produced final result."""

    final_confidence: float = 0.0
    """Confidence of final resolution."""


class StepTimingSummary(BaseModel):
    """Timing summary for a processing step."""

    step_name: str = ""
    """Name of the step (e.g., 's3_download_words_snapshot')."""

    step_type: str = ""
    """Type of step: 's3', 'embedding', 'chroma', 'llm', 'merchant'."""

    duration_ms: float = 0.0
    """Duration in milliseconds."""

    count: int = 0
    """Number of times this step ran (e.g., N for label_validation_chroma)."""
