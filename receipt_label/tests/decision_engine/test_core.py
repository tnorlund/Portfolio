"""Unit tests for Smart Decision Engine core functionality."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from receipt_label.decision_engine import (
    ConfidenceLevel,
    DecisionEngine,
    DecisionEngineConfig,
    DecisionOutcome,
    DecisionResult,
    EssentialFieldsStatus,
    MerchantReliabilityData,
    PatternDetectionSummary,
    create_aggressive_config,
    create_conservative_config)


class TestDecisionEngine:
    """Test cases for the DecisionEngine class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = DecisionEngineConfig(
            enabled=True,
            min_coverage_percentage=90.0,
            max_unlabeled_words=5,
            rollout_percentage=100.0)
        self.engine = DecisionEngine(self.config)

    def test_engine_initialization(self):
        """Test DecisionEngine initialization."""
        assert self.engine.config == self.config
        assert self.engine._decisions_made == 0
        assert self.engine._total_decision_time_ms == 0.0

    def test_decision_engine_disabled(self):
        """Test behavior when decision engine is disabled."""
        config = DecisionEngineConfig(enabled=False)
        engine = DecisionEngine(config)

        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.REQUIRED
        assert "disabled" in result.reasoning.lower()

    def test_missing_critical_fields_requires_gpt(self):
        """Test that missing critical fields always require GPT."""
        # Missing merchant name
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=False,  # Missing critical field
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = self.engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.REQUIRED
        assert result.confidence == ConfidenceLevel.HIGH
        assert "MERCHANT_NAME" in result.reasoning
        assert "MERCHANT_NAME" in result.essential_fields_missing

    def test_perfect_coverage_skip_gpt(self):
        """Test that perfect coverage and all fields skip GPT."""
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = self.engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.SKIP
        assert result.confidence in [
            ConfidenceLevel.HIGH,
            ConfidenceLevel.MEDIUM,
        ]
        assert "high coverage" in result.reasoning.lower()
        assert result.skip_rate_contribution == 1.0

    def test_good_coverage_missing_product_batch(self):
        """Test that good coverage but missing product goes to batch."""
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=92.0,
            unlabeled_words=3,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=False,  # Missing product name
            ))

        result = self.engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.BATCH
        assert result.confidence == ConfidenceLevel.MEDIUM
        assert "missing product name" in result.reasoning.lower()
        assert result.skip_rate_contribution == 0.0

    def test_low_coverage_requires_gpt(self):
        """Test that low coverage requires GPT."""
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=75.0,  # Below 90% threshold
            unlabeled_words=3,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = self.engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.REQUIRED
        assert "low coverage" in result.reasoning.lower()
        assert "< 90.0%" in result.reasoning

    def test_too_many_unlabeled_words_requires_gpt(self):
        """Test that too many unlabeled words requires GPT."""
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=92.0,
            unlabeled_words=10,  # Above 5 word threshold
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = self.engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.REQUIRED
        assert "too many unlabeled words" in result.reasoning.lower()
        assert "10 > 5" in result.reasoning

    def test_merchant_reliability_boost(self):
        """Test that reliable merchants get confidence boost."""
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        reliable_merchant = MerchantReliabilityData(
            merchant_name="Walmart",
            total_receipts_processed=20,
            pattern_only_success_rate=0.9,
            common_labels={"PRODUCT_NAME", "GRAND_TOTAL"},
            rarely_present_labels=set(),
            typical_receipt_structure={},
            last_updated=datetime.now())

        result = self.engine.decide(pattern_summary, reliable_merchant)

        assert result.action == DecisionOutcome.SKIP
        assert result.confidence == ConfidenceLevel.HIGH  # Boosted from MEDIUM
        assert (
            result.merchant_reliability_score
            == reliable_merchant.reliability_score
        )

    def test_rollout_percentage_filtering(self):
        """Test gradual rollout percentage filtering."""
        config = DecisionEngineConfig(
            enabled=True, rollout_percentage=0.0
        )  # 0% rollout
        engine = DecisionEngine(config)

        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.REQUIRED
        assert "outside rollout" in result.reasoning.lower()

    def test_error_handling_defaults_to_gpt(self):
        """Test that errors default to GPT for safety."""
        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        # Mock an error in the decision process
        with patch.object(
            self.engine, "_make_decision", side_effect=Exception("Test error")
        ):
            result = self.engine.decide(pattern_summary)

        assert result.action == DecisionOutcome.REQUIRED
        assert result.confidence == ConfidenceLevel.LOW
        assert "error" in result.reasoning.lower()

    def test_performance_tracking(self):
        """Test that performance metrics are tracked."""
        config = DecisionEngineConfig(
            enabled=True,
            enable_performance_timing=True,
            rollout_percentage=100.0)
        engine = DecisionEngine(config)

        pattern_summary = self._create_pattern_summary(
            coverage_percentage=95.0,
            unlabeled_words=2,
            essential_fields=EssentialFieldsStatus(
                merchant_name_found=True,
                date_found=True,
                grand_total_found=True,
                product_name_found=True))

        result = engine.decide(pattern_summary)

        assert result.decision_time_ms is not None
        assert result.decision_time_ms > 0

        stats = engine.get_performance_stats()
        assert stats["decisions_made"] == 1
        assert stats["average_decision_time_ms"] > 0
        assert stats["total_decision_time_ms"] > 0

    def test_configuration_validation(self):
        """Test configuration validation."""
        # Test invalid coverage percentage
        with pytest.raises(ValueError, match="min_coverage_percentage"):
            DecisionEngineConfig(min_coverage_percentage=150.0).validate()

        # Test invalid confidence threshold
        with pytest.raises(ValueError, match="min_pattern_confidence"):
            DecisionEngineConfig(min_pattern_confidence=1.5).validate()

        # Test invalid rollout percentage
        with pytest.raises(ValueError, match="rollout_percentage"):
            DecisionEngineConfig(rollout_percentage=-10.0).validate()

    def test_conservative_config(self):
        """Test conservative configuration."""
        config = create_conservative_config()

        assert config.min_coverage_percentage == 95.0
        assert config.max_unlabeled_words == 3
        assert config.min_pattern_confidence == 0.8
        assert config.rollout_percentage == 10.0

    def test_aggressive_config(self):
        """Test aggressive configuration."""
        config = create_aggressive_config()

        assert config.min_coverage_percentage == 85.0
        assert config.max_unlabeled_words == 8
        assert config.min_pattern_confidence == 0.6
        assert config.rollout_percentage == 100.0

    def _create_pattern_summary(
        self,
        coverage_percentage: float,
        unlabeled_words: int,
        essential_fields: EssentialFieldsStatus,
        total_words: int = 50,
        detected_merchant: str = "Test Merchant") -> PatternDetectionSummary:
        """Helper to create PatternDetectionSummary for testing."""
        labeled_words = int((total_words * coverage_percentage) / 100)
        noise_words = 5  # Assume 5 noise words
        meaningful_total = total_words - noise_words

        return PatternDetectionSummary(
            total_words=total_words,
            labeled_words=labeled_words,
            noise_words=noise_words,
            meaningful_unlabeled_words=unlabeled_words,
            labels_by_type={
                "CURRENCY": ["12.99", "1.50"],
                "DATE": ["2024-01-15"],
                "MERCHANT_NAME": (
                    [detected_merchant]
                    if essential_fields.merchant_name_found
                    else []
                ),
            },
            confidence_scores={},
            essential_fields=essential_fields,
            detected_merchant=(
                detected_merchant
                if essential_fields.merchant_name_found
                else None
            ),
            merchant_confidence=(
                0.8 if essential_fields.merchant_name_found else None
            ))


class TestEssentialFieldsStatus:
    """Test cases for EssentialFieldsStatus."""

    def test_all_critical_found(self):
        """Test all_critical_found property."""
        status = EssentialFieldsStatus(
            merchant_name_found=True,
            date_found=True,
            grand_total_found=True,
            product_name_found=False)

        assert status.all_critical_found is True
        assert status.all_essential_found is False

    def test_missing_critical_fields(self):
        """Test missing_critical_fields property."""
        status = EssentialFieldsStatus(
            merchant_name_found=False,
            date_found=True,
            grand_total_found=False,
            product_name_found=True)

        missing = status.missing_critical_fields
        assert "MERCHANT_NAME" in missing
        assert "GRAND_TOTAL" in missing
        assert "DATE" not in missing
        assert len(missing) == 2

    def test_found_essential_fields(self):
        """Test found_essential_fields property."""
        status = EssentialFieldsStatus(
            merchant_name_found=True,
            date_found=False,
            grand_total_found=True,
            product_name_found=True)

        found = status.found_essential_fields
        assert "MERCHANT_NAME" in found
        assert "GRAND_TOTAL" in found
        assert "PRODUCT_NAME" in found
        assert "DATE" not in found
        assert len(found) == 3


class TestPatternDetectionSummary:
    """Test cases for PatternDetectionSummary."""

    def test_coverage_percentage_calculation(self):
        """Test coverage percentage calculation."""
        summary = PatternDetectionSummary(
            total_words=100,
            labeled_words=80,
            noise_words=10,
            meaningful_unlabeled_words=10,
            labels_by_type={},
            confidence_scores={},
            essential_fields=EssentialFieldsStatus())

        # Meaningful words = 100 - 10 = 90
        # Coverage = 80 / 90 = 88.89%
        assert abs(summary.coverage_percentage - 88.89) < 0.01

    def test_coverage_helpers(self):
        """Test coverage helper methods."""
        summary = PatternDetectionSummary(
            total_words=100,
            labeled_words=85,
            noise_words=10,
            meaningful_unlabeled_words=5,
            labels_by_type={},
            confidence_scores={},
            essential_fields=EssentialFieldsStatus())

        # Coverage = 85 / 90 = 94.44%
        assert summary.is_high_coverage(90.0) is True
        assert summary.is_high_coverage(95.0) is False
        assert summary.has_few_unlabeled_words(5) is True
        assert summary.has_few_unlabeled_words(4) is False


class TestMerchantReliabilityData:
    """Test cases for MerchantReliabilityData."""

    def test_reliability_calculation(self):
        """Test reliability score calculation."""
        data = MerchantReliabilityData(
            merchant_name="Test Merchant",
            total_receipts_processed=20,
            pattern_only_success_rate=0.8,
            common_labels=set(),
            rarely_present_labels=set(),
            typical_receipt_structure={},
            last_updated=datetime.now())

        assert data.is_reliable is True
        assert (
            data.reliability_score == 0.8
        )  # 20 receipts gives full confidence

    def test_low_data_reliability(self):
        """Test reliability with low data points."""
        data = MerchantReliabilityData(
            merchant_name="New Merchant",
            total_receipts_processed=2,
            pattern_only_success_rate=0.9,
            common_labels=set(),
            rarely_present_labels=set(),
            typical_receipt_structure={},
            last_updated=datetime.now())

        assert data.is_reliable is False  # Not enough receipts
        # Reliability = 0.9 * (2/20) = 0.09
        assert abs(data.reliability_score - 0.09) < 0.01
