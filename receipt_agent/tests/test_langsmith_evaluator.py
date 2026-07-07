"""Unit tests for LangSmith custom evaluator functions."""

from datetime import UTC, datetime

import pytest
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.langsmith_evaluator import (
    EvaluationQualityMetrics,
    LabelComparisonMetrics,
    _compare_labels,
    _evaluate_prediction_quality,
    create_label_evaluator,
    label_accuracy_evaluator,
    label_quality_evaluator,
)

# Use a valid UUIDv4 for tests
TEST_IMAGE_ID = "a1b2c3d4-e5f6-47a8-89b0-c1d2e3f4a5b6"


# Helper to create sample bounding box and corners
def _make_word_geometry(x: float, y: float, w: float, h: float) -> dict:
    """Create geometry dicts for a word at (x, y) with width w and height h."""
    return {
        "bounding_box": {
            "x": x - w / 2,
            "y": y - h / 2,
            "width": w,
            "height": h,
        },
        "top_left": {"x": x - w / 2, "y": y - h / 2},
        "top_right": {"x": x + w / 2, "y": y - h / 2},
        "bottom_left": {"x": x - w / 2, "y": y + h / 2},
        "bottom_right": {"x": x + w / 2, "y": y + h / 2},
    }


# Fixtures for test data
@pytest.fixture
def sample_words() -> list[ReceiptWord]:
    """Create sample receipt words for testing."""
    return [
        ReceiptWord(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="ACME",
            **_make_word_geometry(0.5, 0.95, 0.1, 0.02),
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.99,
        ),
        ReceiptWord(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=2,
            text="Widget",
            **_make_word_geometry(0.2, 0.5, 0.15, 0.02),
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.98,
        ),
        ReceiptWord(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=3,
            text="5.99",
            **_make_word_geometry(0.8, 0.5, 0.1, 0.02),
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.99,
        ),
        ReceiptWord(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=4,
            text="TOTAL",
            **_make_word_geometry(0.2, 0.1, 0.12, 0.02),
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.99,
        ),
        ReceiptWord(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=5,
            text="5.99",
            **_make_word_geometry(0.8, 0.1, 0.1, 0.02),
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.99,
        ),
    ]


@pytest.fixture
def ground_truth_labels() -> list[ReceiptWordLabel]:
    """Create ground truth labels (VALID only)."""
    now = datetime.now(UTC)
    return [
        ReceiptWordLabel(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="MERCHANT_NAME",
            reasoning="Ground truth",
            validation_status="VALID",
            timestamp_added=now,
        ),
        ReceiptWordLabel(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=2,
            label="PRODUCT_NAME",
            reasoning="Ground truth",
            validation_status="VALID",
            timestamp_added=now,
        ),
        ReceiptWordLabel(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=3,
            label="LINE_TOTAL",
            reasoning="Ground truth",
            validation_status="VALID",
            timestamp_added=now,
        ),
        ReceiptWordLabel(
            image_id=TEST_IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=5,
            label="GRAND_TOTAL",
            reasoning="Ground truth",
            validation_status="VALID",
            timestamp_added=now,
        ),
    ]


class TestLabelComparisonMetrics:
    """Tests for LabelComparisonMetrics dataclass."""

    def test_precision_with_no_predictions(self) -> None:
        """Precision should be 0 when no predictions."""
        metrics = LabelComparisonMetrics(
            true_positives=0,
            false_positives=0,
            false_negatives=5,
        )
        assert metrics.precision == 0.0

    def test_precision_with_all_correct(self) -> None:
        """Precision should be 1.0 when all predictions are correct."""
        metrics = LabelComparisonMetrics(
            true_positives=5,
            false_positives=0,
            false_negatives=0,
        )
        assert metrics.precision == 1.0

    def test_recall_with_no_ground_truth(self) -> None:
        """Recall should be 0 when no ground truth."""
        metrics = LabelComparisonMetrics(
            true_positives=0,
            false_positives=5,
            false_negatives=0,
        )
        assert metrics.recall == 0.0

    def test_f1_score(self) -> None:
        """F1 should be harmonic mean of precision and recall."""
        metrics = LabelComparisonMetrics(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            total_predictions=5,
            total_ground_truth=5,
        )
        # precision = 4/5 = 0.8, recall = 4/5 = 0.8
        # f1 = 2 * 0.8 * 0.8 / (0.8 + 0.8) = 0.8
        assert metrics.f1_score == pytest.approx(0.8)


class TestEvaluationQualityMetrics:
    """Tests for EvaluationQualityMetrics dataclass."""

    def test_issue_rate_with_no_words(self) -> None:
        """Issue rate should be 0 when no words."""
        metrics = EvaluationQualityMetrics(total_issues=0, total_words=0)
        assert metrics.issue_rate == 0.0

    def test_issue_rate_calculation(self) -> None:
        """Issue rate should be issues/words."""
        metrics = EvaluationQualityMetrics(total_issues=2, total_words=10)
        assert metrics.issue_rate == pytest.approx(0.2)

    def test_get_issue_rate_by_type(self) -> None:
        """Should return rate for specific issue type."""
        metrics = EvaluationQualityMetrics(
            total_issues=3,
            total_words=10,
            issues_by_type={"position_anomaly": 2, "geometric_anomaly": 1},
        )
        assert metrics.get_issue_rate("position_anomaly") == pytest.approx(0.2)
        assert metrics.get_issue_rate("geometric_anomaly") == pytest.approx(
            0.1
        )
        assert metrics.get_issue_rate("unknown") == 0.0


class TestCompareLabels:
    """Tests for _compare_labels function."""

    def test_perfect_match(
        self, ground_truth_labels: list[ReceiptWordLabel]
    ) -> None:
        """Should return perfect scores when predictions match ground truth."""
        # Use same labels as predictions
        predicted = ground_truth_labels.copy()

        metrics = _compare_labels(predicted, ground_truth_labels)

        assert metrics.true_positives == 4
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 0
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1_score == 1.0

    def test_no_predictions(
        self, ground_truth_labels: list[ReceiptWordLabel]
    ) -> None:
        """Should return 0 precision/recall when no predictions."""
        metrics = _compare_labels([], ground_truth_labels)

        assert metrics.true_positives == 0
        assert metrics.false_negatives == 4  # All ground truth missed
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0

    def test_wrong_label_type(
        self, ground_truth_labels: list[ReceiptWordLabel]
    ) -> None:
        """Wrong label type should count as FP and FN."""
        now = datetime.now(UTC)
        predicted = [
            ReceiptWordLabel(
                image_id=TEST_IMAGE_ID,
                receipt_id=1,
                line_id=1,
                word_id=1,
                label="ADDRESS_LINE",  # Wrong! Should be MERCHANT_NAME
                reasoning="Test prediction",
                validation_status="VALID",
                timestamp_added=now,
            ),
        ]

        metrics = _compare_labels(predicted, ground_truth_labels)

        assert metrics.true_positives == 0
        assert metrics.false_positives == 1  # Wrong label
        assert metrics.false_negatives == 4  # All ground truth missed/wrong

    def test_filters_non_valid_ground_truth(self) -> None:
        """Should only compare against VALID ground truth labels."""
        now = datetime.now(UTC)
        ground_truth = [
            ReceiptWordLabel(
                image_id=TEST_IMAGE_ID,
                receipt_id=1,
                line_id=1,
                word_id=1,
                label="MERCHANT_NAME",
                reasoning="Ground truth",
                validation_status="VALID",
                timestamp_added=now,
            ),
            ReceiptWordLabel(
                image_id=TEST_IMAGE_ID,
                receipt_id=1,
                line_id=1,
                word_id=2,
                label="PRODUCT_NAME",
                reasoning="Ground truth",
                validation_status="INVALID",  # Should be ignored
                timestamp_added=now,
            ),
        ]
        predicted = [
            ReceiptWordLabel(
                image_id=TEST_IMAGE_ID,
                receipt_id=1,
                line_id=1,
                word_id=1,
                label="MERCHANT_NAME",
                reasoning="Predicted",
                validation_status="VALID",
                timestamp_added=now,
            ),
        ]

        metrics = _compare_labels(predicted, ground_truth)

        # Only 1 VALID ground truth, 1 correct prediction
        assert metrics.true_positives == 1
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 0
        assert metrics.total_ground_truth == 1


class TestEvaluatePredictionQuality:
    """Tests for _evaluate_prediction_quality function."""

    def test_with_no_words(self) -> None:
        """Should return empty metrics when no words."""
        metrics = _evaluate_prediction_quality([], [], None)
        assert metrics.total_words == 0
        assert metrics.total_issues == 0

    def test_with_words_no_labels(
        self, sample_words: list[ReceiptWord]
    ) -> None:
        """Should detect missing labels when words have no labels."""
        metrics = _evaluate_prediction_quality(sample_words, [], None)
        assert metrics.total_words == 5


class TestLabelAccuracyEvaluator:
    """Tests for label_accuracy_evaluator function."""

    def test_returns_langsmith_format(
        self, ground_truth_labels: list[ReceiptWordLabel]
    ) -> None:
        """Should return dict with key, score, comment."""
        result = label_accuracy_evaluator(
            inputs={},
            outputs={"labels": ground_truth_labels},
            reference_outputs={"labels": ground_truth_labels},
        )

        assert "key" in result
        assert result["key"] == "label_accuracy"
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert "comment" in result

    def test_handles_dict_input(self) -> None:
        """Should handle labels as dicts (from JSON)."""
        now = datetime.now(UTC)
        label_dict = {
            "image_id": TEST_IMAGE_ID,
            "receipt_id": 1,
            "line_id": 1,
            "word_id": 1,
            "label": "MERCHANT_NAME",
            "reasoning": "Test",
            "validation_status": "VALID",
            "timestamp_added": now,
        }

        result = label_accuracy_evaluator(
            inputs={},
            outputs={"labels": [label_dict]},
            reference_outputs={"labels": [label_dict]},
        )

        assert result["score"] == 1.0  # Perfect match


class TestLabelQualityEvaluator:
    """Tests for label_quality_evaluator function."""

    def test_returns_langsmith_format(
        self,
        sample_words: list[ReceiptWord],
        ground_truth_labels: list[ReceiptWordLabel],
    ) -> None:
        """Should return dict with key, score, comment, metadata."""
        result = label_quality_evaluator(
            inputs={"words": sample_words},
            outputs={"labels": ground_truth_labels},
            reference_outputs={"labels": ground_truth_labels},
        )

        assert "key" in result
        assert result["key"] == "label_quality"
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert "comment" in result
        assert "metadata" in result
        assert "precision" in result["metadata"]
        assert "recall" in result["metadata"]
        assert "f1_score" in result["metadata"]
        assert "total_issues" in result["metadata"]
        assert "issues_by_type" in result["metadata"]

    def test_combined_score_weighting(
        self,
        sample_words: list[ReceiptWord],
        ground_truth_labels: list[ReceiptWordLabel],
    ) -> None:
        """Combined score should weight F1 at 70% and quality at 30%."""
        result = label_quality_evaluator(
            inputs={"words": sample_words},
            outputs={"labels": ground_truth_labels},
            reference_outputs={"labels": ground_truth_labels},
        )

        f1 = result["metadata"]["f1_score"]
        issue_rate = result["metadata"]["issue_rate"]
        expected_score = 0.7 * f1 + 0.3 * (1.0 - issue_rate)

        assert result["score"] == pytest.approx(expected_score, rel=0.01)


class TestCreateLabelEvaluator:
    """Tests for create_label_evaluator factory function."""

    def test_returns_accuracy_evaluator_when_quality_disabled(self) -> None:
        """Should return accuracy evaluator when quality check disabled."""
        evaluator = create_label_evaluator(enable_quality_check=False)
        assert evaluator == label_accuracy_evaluator

    def test_returns_callable_with_patterns(self) -> None:
        """Should return callable evaluator when patterns provided."""
        evaluator = create_label_evaluator(patterns=None)
        assert callable(evaluator)

    def test_partial_function_has_patterns(self) -> None:
        """Factory should create partial with patterns bound."""
        from receipt_agent.agents.label_evaluator.state import MerchantPatterns

        patterns = MerchantPatterns(
            merchant_name="Test",
            receipt_count=5,
            label_positions={},
            label_pair_geometry={},
            all_observed_pairs=set(),
        )
        evaluator = create_label_evaluator(patterns=patterns)

        # Verify it's a partial with patterns
        assert hasattr(evaluator, "keywords")
        assert evaluator.keywords.get("patterns") is patterns
