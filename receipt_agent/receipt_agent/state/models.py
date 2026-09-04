"""
Pydantic models for agent state management.

These models define shared, typed result structures used by receipt_agent
workflows, ensuring type safety and validation at runtime.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

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

    VECTOR_SIMILARITY = "vector_similarity"
    PLACE_ID_MATCH = "place_id_match"
    PHONE_MATCH = "phone_match"
    ADDRESS_MATCH = "address_match"
    CROSS_RECEIPT = "cross_receipt"
    GOOGLE_PLACES = "google_places"
    LLM_REASONING = "llm_reasoning"


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
        description="Source of this candidate (vector/places/llm)"
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
        description="Whether line embeddings exist in the vector index",
    )
    word_embeddings_available: bool = Field(
        default=False,
        description="Whether word embeddings exist in the vector index",
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
