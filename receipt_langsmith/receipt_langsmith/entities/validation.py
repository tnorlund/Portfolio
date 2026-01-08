"""ValidationAgent trace schemas.

This module defines schemas for parsing ValidationAgent trace inputs/outputs
from LangSmith exports.
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

    CHROMA_SIMILARITY = "chroma_similarity"
    PLACE_ID_MATCH = "place_id_match"
    PHONE_MATCH = "phone_match"
    ADDRESS_MATCH = "address_match"
    CROSS_RECEIPT = "cross_receipt"
    GOOGLE_PLACES = "google_places"
    LLM_REASONING = "llm_reasoning"


class MerchantCandidateTrace(BaseModel):
    """Merchant candidate from trace outputs."""

    merchant_name: str
    """Merchant name."""

    place_id: Optional[str] = None
    """Google Place ID."""

    address: Optional[str] = None
    """Business address."""

    phone_number: Optional[str] = None
    """Phone number."""

    category: Optional[str] = None
    """Business category."""

    confidence_score: float = Field(ge=0.0, le=1.0)
    """Confidence in this candidate (0-1)."""

    source: str = ""
    """Source of this candidate (chroma/places/llm)."""

    matched_fields: list[str] = Field(default_factory=list)
    """Fields that matched."""


class VerificationEvidenceTrace(BaseModel):
    """Evidence supporting a verification decision."""

    evidence_type: str
    """Type of evidence (see EvidenceType enum)."""

    description: str
    """Human-readable description."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Confidence in this evidence (0-1)."""

    supporting_data: dict[str, Any] = Field(default_factory=dict)
    """Raw data supporting this evidence."""


class VerificationStepTrace(BaseModel):
    """A single step in the verification process."""

    step_name: str
    """Name of the verification step."""

    question: str
    """Question being answered."""

    answer: Optional[str] = None
    """Answer found."""

    evidence: list[VerificationEvidenceTrace] = Field(default_factory=list)
    """Evidence collected."""

    passed: Optional[bool] = None
    """Whether this step passed verification."""

    reasoning: str = ""
    """LLM reasoning for this step."""


class ValidationResultTrace(BaseModel):
    """Final validation result from trace."""

    status: ValidationStatus
    """Validation status."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Overall confidence score (0-1)."""

    validated_merchant: Optional[MerchantCandidateTrace] = None
    """Validated merchant information."""

    verification_steps: list[VerificationStepTrace] = Field(default_factory=list)
    """Steps taken during verification."""

    evidence_summary: list[VerificationEvidenceTrace] = Field(default_factory=list)
    """Summary of all evidence collected."""

    reasoning: str = ""
    """Final reasoning for validation decision."""

    recommendations: list[str] = Field(default_factory=list)
    """Recommendations for metadata updates."""

    timestamp: Optional[datetime] = None
    """When validation was performed."""


class ValidationAgentInputs(BaseModel):
    """Inputs to ValidationAgent trace.

    These are the parameters passed when invoking the validation workflow.
    """

    image_id: str
    """Receipt image ID (UUID)."""

    receipt_id: int
    """Receipt ID within image (1-indexed)."""


class ValidationAgentOutputs(BaseModel):
    """Outputs from ValidationAgent trace.

    These are the results returned by the validation workflow.
    """

    result: Optional[ValidationResultTrace] = None
    """Final validation result."""

    merchant_candidates: list[MerchantCandidateTrace] = Field(default_factory=list)
    """Candidate merchants found during validation."""

    verification_steps: list[VerificationStepTrace] = Field(default_factory=list)
    """Verification steps completed."""

    errors: list[str] = Field(default_factory=list)
    """Errors encountered during validation."""

    current_step: str = ""
    """Last step reached in workflow."""
