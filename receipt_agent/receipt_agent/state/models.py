"""
Pydantic models for agent state management.

These models define the typed state that flows through the LangGraph workflow,
ensuring type safety and validation at runtime.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class ValidationStatus(str, Enum):
    """Status of metadata validation."""

    PENDING = "pending"
    VALIDATED = "validated"
    INVALID = "invalid"
    NEEDS_REVIEW = "needs_review"
    ERROR = "error"


class EvidenceType(str, Enum):
    """Type of verification evidence."""

    CHROMA_SIMILARITY = "chroma_similarity"
    PLACE_ID_MATCH = "place_id_match"
    PHONE_MATCH = "phone_match"
    ADDRESS_MATCH = "address_match"
    CROSS_RECEIPT = "cross_receipt"
    GOOGLE_PLACES = "google_places"
    LLM_REASONING = "llm_reasoning"


class ChromaSearchResult(BaseModel):
    """Result from a ChromaDB similarity search."""

    chroma_id: str = Field(description="ChromaDB document ID")
    image_id: str = Field(description="Receipt image ID")
    receipt_id: int = Field(description="Receipt ID within image")
    similarity_score: float = Field(
        description="Cosine similarity score",
        ge=0.0,
        le=1.0,
    )
    document_text: Optional[str] = Field(
        default=None,
        description="Original document text",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="ChromaDB metadata",
    )


class MerchantCandidate(BaseModel):
    """A candidate merchant match."""

    merchant_name: str = Field(description="Merchant name")
    place_id: Optional[str] = Field(
        default=None,
        description="Google Place ID",
    )
    address: Optional[str] = Field(
        default=None,
        description="Business address",
    )
    phone_number: Optional[str] = Field(
        default=None,
        description="Phone number",
    )
    category: Optional[str] = Field(
        default=None,
        description="Business category",
    )
    confidence_score: float = Field(
        description="Confidence in this candidate",
        ge=0.0,
        le=1.0,
    )
    source: str = Field(
        description="Source of this candidate (chroma/places/llm)"
    )
    matched_fields: list[str] = Field(
        default_factory=list,
        description="Fields that matched",
    )


class VerificationEvidence(BaseModel):
    """Evidence supporting a verification decision."""

    evidence_type: EvidenceType = Field(description="Type of evidence")
    description: str = Field(description="Human-readable description")
    confidence: float = Field(
        description="Confidence in this evidence",
        ge=0.0,
        le=1.0,
    )
    supporting_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw data supporting this evidence",
    )


class VerificationStep(BaseModel):
    """A single step in the verification process."""

    step_name: str = Field(description="Name of the verification step")
    question: str = Field(description="Question being answered")
    answer: Optional[str] = Field(default=None, description="Answer found")
    evidence: list[VerificationEvidence] = Field(
        default_factory=list,
        description="Evidence collected",
    )
    passed: Optional[bool] = Field(
        default=None,
        description="Whether this step passed verification",
    )
    reasoning: str = Field(
        default="",
        description="LLM reasoning for this step",
    )


class ReceiptContext(BaseModel):
    """Context about the receipt being validated."""

    image_id: str = Field(description="Receipt image ID")
    receipt_id: int = Field(description="Receipt ID within image")
    raw_text: list[str] = Field(
        default_factory=list,
        description="Raw text lines from receipt",
    )
    extracted_merchant_name: Optional[str] = Field(
        default=None,
        description="Merchant name extracted from receipt",
    )
    extracted_address: Optional[str] = Field(
        default=None,
        description="Address extracted from receipt",
    )
    extracted_phone: Optional[str] = Field(
        default=None,
        description="Phone extracted from receipt",
    )
    line_embeddings_available: bool = Field(
        default=False,
        description="Whether line embeddings exist in ChromaDB",
    )
    word_embeddings_available: bool = Field(
        default=False,
        description="Whether word embeddings exist in ChromaDB",
    )


class ValidationResult(BaseModel):
    """Final validation result for receipt metadata."""

    status: ValidationStatus = Field(description="Validation status")
    confidence: float = Field(
        description="Overall confidence score",
        ge=0.0,
        le=1.0,
    )
    validated_merchant: Optional[MerchantCandidate] = Field(
        default=None,
        description="Validated merchant information",
    )
    verification_steps: list[VerificationStep] = Field(
        default_factory=list,
        description="Steps taken during verification",
    )
    evidence_summary: list[VerificationEvidence] = Field(
        default_factory=list,
        description="Summary of all evidence collected",
    )
    reasoning: str = Field(
        default="",
        description="Final reasoning for validation decision",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations for metadata updates",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When validation was performed",
    )


class ValidationState(BaseModel):
    """
    Main state object for the validation workflow.

    This is the state that flows through the LangGraph workflow,
    accumulating information as the agent validates receipt metadata.
    """

    # Input: Receipt metadata to validate
    image_id: str = Field(description="Receipt image ID")
    receipt_id: int = Field(description="Receipt ID within image")

    # Current metadata (from DynamoDB)
    current_merchant_name: Optional[str] = Field(
        default=None,
        description="Current merchant name in metadata",
    )
    current_place_id: Optional[str] = Field(
        default=None,
        description="Current Google Place ID",
    )
    current_address: Optional[str] = Field(
        default=None,
        description="Current address in metadata",
    )
    current_phone: Optional[str] = Field(
        default=None,
        description="Current phone in metadata",
    )
    current_validation_status: Optional[str] = Field(
        default=None,
        description="Current validation status",
    )

    # Context loaded from receipt
    receipt_context: Optional[ReceiptContext] = Field(
        default=None,
        description="Context about the receipt",
    )

    # Retrieved embeddings from ChromaDB (keyed by ChromaDB document ID)
    receipt_line_embeddings: dict[str, list[float]] = Field(
        default_factory=dict,
        description="Line embeddings retrieved from ChromaDB by document ID",
    )
    receipt_word_embeddings: dict[str, list[float]] = Field(
        default_factory=dict,
        description="Word embeddings retrieved from ChromaDB by document ID",
    )

    # Agent conversation messages
    messages: Annotated[list[Any], add_messages] = Field(
        default_factory=list,
        description="Agent conversation messages",
    )

    # ChromaDB search results
    chroma_line_results: list[ChromaSearchResult] = Field(
        default_factory=list,
        description="Similar lines from ChromaDB",
    )
    chroma_word_results: list[ChromaSearchResult] = Field(
        default_factory=list,
        description="Similar words from ChromaDB",
    )

    # Merchant candidates found
    merchant_candidates: list[MerchantCandidate] = Field(
        default_factory=list,
        description="Candidate merchants found",
    )

    # Verification progress
    verification_steps: list[VerificationStep] = Field(
        default_factory=list,
        description="Verification steps completed",
    )

    # Agent state
    current_step: str = Field(
        default="start",
        description="Current step in workflow",
    )
    iteration_count: int = Field(
        default=0,
        description="Number of agent iterations",
    )
    should_continue: bool = Field(
        default=True,
        description="Whether agent should continue",
    )

    # Final output
    result: Optional[ValidationResult] = Field(
        default=None,
        description="Final validation result",
    )

    # Error handling
    errors: list[str] = Field(
        default_factory=list,
        description="Errors encountered during validation",
    )


class ToolCall(BaseModel):
    """Represents a tool call made by the agent."""

    tool_name: str = Field(description="Name of the tool called")
    tool_input: dict[str, Any] = Field(description="Input to the tool")
    tool_output: Optional[Any] = Field(
        default=None,
        description="Output from tool",
    )
    success: bool = Field(
        default=True,
        description="Whether tool call succeeded",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if failed",
    )
