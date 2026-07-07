"""Tests for state models."""

from datetime import datetime

import pytest

from receipt_agent.state.models import (
    ChromaSearchResult,
    EvidenceType,
    MerchantCandidate,
    ReceiptContext,
    ValidationResult,
    ValidationState,
    ValidationStatus,
    VerificationEvidence,
    VerificationStep,
)


class TestValidationStatus:
    """Tests for ValidationStatus enum."""

    def test_enum_values(self):
        assert ValidationStatus.PENDING.value == "pending"
        assert ValidationStatus.VALIDATED.value == "validated"
        assert ValidationStatus.INVALID.value == "invalid"
        assert ValidationStatus.NEEDS_REVIEW.value == "needs_review"
        assert ValidationStatus.ERROR.value == "error"


class TestChromaSearchResult:
    """Tests for ChromaSearchResult model."""

    def test_create_result(self):
        result = ChromaSearchResult(
            chroma_id="test-id",
            image_id="image-123",
            receipt_id=1,
            similarity_score=0.85,
            document_text="Test document",
            metadata={"key": "value"},
        )

        assert result.chroma_id == "test-id"
        assert result.image_id == "image-123"
        assert result.receipt_id == 1
        assert result.similarity_score == 0.85
        assert result.document_text == "Test document"
        assert result.metadata == {"key": "value"}

    def test_similarity_score_bounds(self):
        # Valid range
        result = ChromaSearchResult(
            chroma_id="test",
            image_id="img",
            receipt_id=1,
            similarity_score=0.0,
        )
        assert result.similarity_score == 0.0

        result = ChromaSearchResult(
            chroma_id="test",
            image_id="img",
            receipt_id=1,
            similarity_score=1.0,
        )
        assert result.similarity_score == 1.0

    def test_invalid_similarity_score(self):
        with pytest.raises(ValueError):
            ChromaSearchResult(
                chroma_id="test",
                image_id="img",
                receipt_id=1,
                similarity_score=1.5,  # Invalid
            )


class TestMerchantCandidate:
    """Tests for MerchantCandidate model."""

    def test_create_candidate(self):
        candidate = MerchantCandidate(
            merchant_name="Starbucks",
            place_id="ChIJ123",
            address="123 Coffee St",
            phone_number="555-1234",
            category="Coffee Shop",
            confidence_score=0.9,
            source="chroma",
            matched_fields=["name", "phone"],
        )

        assert candidate.merchant_name == "Starbucks"
        assert candidate.place_id == "ChIJ123"
        assert candidate.confidence_score == 0.9
        assert candidate.source == "chroma"
        assert "name" in candidate.matched_fields


class TestVerificationStep:
    """Tests for VerificationStep model."""

    def test_create_step(self):
        step = VerificationStep(
            step_name="phone_check",
            question="Does the phone number match?",
            answer="Yes, phone matches exactly",
            passed=True,
            reasoning="Phone digits are identical",
        )

        assert step.step_name == "phone_check"
        assert step.question == "Does the phone number match?"
        assert step.passed is True

    def test_step_with_evidence(self):
        evidence = VerificationEvidence(
            evidence_type=EvidenceType.PHONE_MATCH,
            description="Phone numbers match",
            confidence=0.95,
            supporting_data={"matched_digits": "5551234567"},
        )

        step = VerificationStep(
            step_name="phone_check",
            question="Does the phone number match?",
            evidence=[evidence],
            passed=True,
        )

        assert len(step.evidence) == 1
        assert step.evidence[0].evidence_type == EvidenceType.PHONE_MATCH


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_create_result(self):
        result = ValidationResult(
            status=ValidationStatus.VALIDATED,
            confidence=0.85,
            reasoning="All checks passed",
        )

        assert result.status == ValidationStatus.VALIDATED
        assert result.confidence == 0.85
        assert result.reasoning == "All checks passed"
        assert isinstance(result.timestamp, datetime)

    def test_result_with_merchant(self):
        merchant = MerchantCandidate(
            merchant_name="Test",
            confidence_score=0.9,
            source="chroma",
        )

        result = ValidationResult(
            status=ValidationStatus.VALIDATED,
            confidence=0.9,
            validated_merchant=merchant,
        )

        assert result.validated_merchant is not None
        assert result.validated_merchant.merchant_name == "Test"


class TestValidationState:
    """Tests for ValidationState model."""

    def test_create_state(self):
        state = ValidationState(
            image_id="test-image",
            receipt_id=1,
        )

        assert state.image_id == "test-image"
        assert state.receipt_id == 1
        assert state.current_step == "start"
        assert state.should_continue is True
        assert state.iteration_count == 0

    def test_state_with_full_data(self):
        state = ValidationState(
            image_id="test-image",
            receipt_id=1,
            current_merchant_name="Test Merchant",
            current_place_id="ChIJ123",
            current_address="123 Test St",
            current_phone="555-1234",
            current_validation_status="MATCHED",
        )

        assert state.current_merchant_name == "Test Merchant"
        assert state.current_place_id == "ChIJ123"

    def test_state_with_results(self):
        chroma_result = ChromaSearchResult(
            chroma_id="test",
            image_id="other",
            receipt_id=2,
            similarity_score=0.8,
        )

        state = ValidationState(
            image_id="test-image",
            receipt_id=1,
            chroma_line_results=[chroma_result],
        )

        assert len(state.chroma_line_results) == 1
        assert state.chroma_line_results[0].similarity_score == 0.8
