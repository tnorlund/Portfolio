"""Tests for state models."""

from datetime import datetime

from receipt_agent.state.models import (
    EvidenceType,
    MerchantCandidate,
    ValidationResult,
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
            source="places",
            matched_fields=["name", "phone"],
        )

        assert candidate.merchant_name == "Starbucks"
        assert candidate.place_id == "ChIJ123"
        assert candidate.confidence_score == 0.9
        assert candidate.source == "places"
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
            source="places",
        )

        result = ValidationResult(
            status=ValidationStatus.VALIDATED,
            confidence=0.9,
            validated_merchant=merchant,
        )

        assert result.validated_merchant is not None
        assert result.validated_merchant.merchant_name == "Test"
