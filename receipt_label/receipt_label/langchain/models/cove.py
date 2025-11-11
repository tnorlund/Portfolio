"""
Chain of Verification (CoVe) models for self-verification of LLM outputs.

CoVe improves accuracy by:
1. Generating an initial answer
2. Generating verification questions about that answer
3. Answering those questions by checking against source material
4. Revising the original answer based on verification results
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class VerificationQuestion(BaseModel):
    """A question to verify a specific claim in the initial answer."""

    question: str = Field(
        ...,
        description="A specific question that can verify a claim in the initial answer (e.g., 'Is the amount 24.01 actually at the bottom of the receipt?')"
    )
    claim_to_verify: str = Field(
        ...,
        description="The specific claim from the initial answer this question verifies (e.g., 'GRAND_TOTAL: 24.01 at line 29')"
    )
    importance: float = Field(
        ge=0.0,
        le=1.0,
        description="How critical this verification is (1.0 = critical, 0.0 = minor)"
    )


class VerificationAnswer(BaseModel):
    """Answer to a verification question."""

    question: str = Field(..., description="The original verification question")
    answer: str = Field(
        ...,
        description="The answer found by checking the receipt text (e.g., 'Yes, line 29 contains 24.01 and is at the bottom')"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this verification answer"
    )
    evidence: str = Field(
        ...,
        description="Specific evidence from the receipt text that supports this answer"
    )
    requires_revision: bool = Field(
        ...,
        description="Whether the original claim needs to be revised based on this verification"
    )


class VerificationQuestionsResponse(BaseModel):
    """Response containing verification questions for an initial answer."""

    questions: List[VerificationQuestion] = Field(
        ...,
        description="List of verification questions to check the initial answer"
    )
    reasoning: str = Field(
        ...,
        description="Explanation of why these questions were chosen"
    )


class VerificationAnswersResponse(BaseModel):
    """Response containing answers to verification questions."""

    answers: List[VerificationAnswer] = Field(
        ...,
        description="Answers to all verification questions"
    )
    overall_assessment: str = Field(
        ...,
        description="Overall assessment of the initial answer's accuracy"
    )
    revision_needed: bool = Field(
        ...,
        description="Whether the initial answer needs revision"
    )

