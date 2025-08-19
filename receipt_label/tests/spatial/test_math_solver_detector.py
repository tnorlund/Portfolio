"""
Unit tests for MathSolverDetector - Phase 2 mathematical currency detection.

Tests the pure mathematical approach to line item detection that finds which
numbers sum to other numbers without keywords or heuristics.
"""

from unittest.mock import Mock, patch

import pytest

from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.math_solver_detector import (
    MathSolution,
    MathSolverDetector,
)


def create_pattern_match(mock_word, pattern_type, value):
    """Helper function to create PatternMatch objects for testing."""
    return PatternMatch(
        word=mock_word,
        pattern_type=pattern_type,
        confidence=0.9,
        matched_text=str(value),
        extracted_value=str(value),
        metadata={},
    )


class TestMathSolverDetector:
    """Test cases for MathSolverDetector functionality."""

    @pytest.fixture
    def detector(self):
        """Create a MathSolverDetector with default settings."""
        return MathSolverDetector(
            tolerance=0.02, max_solutions=50, use_numpy_optimization=True
        )

    @pytest.fixture
    def legacy_detector(self):
        """Create a MathSolverDetector with NumPy optimization disabled."""
        return MathSolverDetector(
            tolerance=0.02, max_solutions=50, use_numpy_optimization=False
        )

    @pytest.fixture
    def sample_currency_values(self):
        """Create sample currency values for testing."""
        mock_word = Mock()
        mock_word.text = "5.99"
        mock_word.line_id = 1

        return [
            (
                2.99,
                create_pattern_match(mock_word, PatternType.CURRENCY, "2.99"),
            ),
            (
                4.50,
                create_pattern_match(mock_word, PatternType.CURRENCY, "4.50"),
            ),
            (0.60, create_pattern_match(mock_word, PatternType.TAX, "0.60")),
            (
                8.09,
                create_pattern_match(
                    mock_word, PatternType.GRAND_TOTAL, "8.09"
                ),
            ),
        ]

    @pytest.mark.unit
    def test_solve_receipt_math_basic_direct_sum(
        self, detector, sample_currency_values
    ):
        """Test that algorithm finds valid mathematical solutions."""
        solutions = detector.solve_receipt_math(sample_currency_values)

        assert len(solutions) > 0

        # Should find at least one valid solution
        best_solution = solutions[0]
        assert len(best_solution.item_prices) >= 2
        assert best_solution.confidence > 0.7

        # Check that we can find both tax and non-tax solutions
        tax_solutions = [s for s in solutions if s.tax is not None]

        # Algorithm should find the tax structure solution (2.99 + 4.50 + 0.60 = 8.09)
        assert len(tax_solutions) > 0

    @pytest.mark.unit
    def test_solve_receipt_math_with_tax_structure(
        self, detector, sample_currency_values
    ):
        """Test solution with tax structure: items + tax = total."""
        solutions = detector.solve_receipt_math(sample_currency_values)

        # Should find solution: 2.99 + 4.50 + 0.60 = 8.09
        tax_solutions = [s for s in solutions if s.tax is not None]
        assert len(tax_solutions) > 0

        best_tax_solution = tax_solutions[0]
        assert best_tax_solution.tax[0] == 0.60  # Tax amount
        assert (
            best_tax_solution.confidence > 0.8
        )  # Tax structure has higher confidence

    @pytest.mark.unit
    def test_solve_receipt_math_no_solutions(self, detector):
        """Test case with no valid mathematical solutions."""
        # Create values that don't sum to anything meaningful
        mock_word = Mock()
        mock_word.text = "1.00"
        mock_word.line_id = 1

        currency_values = [
            (
                1.11,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="1.11",
                    extracted_value="1.11",
                    metadata={},
                ),
            ),
            (
                2.22,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="2.22",
                    extracted_value="2.22",
                    metadata={},
                ),
            ),
            (
                9.99,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="9.99",
                    extracted_value="9.99",
                    metadata={},
                ),
            ),
        ]

        solutions = detector.solve_receipt_math(currency_values)

        # Should have no solutions or very low confidence solutions
        high_confidence_solutions = [
            s for s in solutions if s.confidence > 0.7
        ]
        assert len(high_confidence_solutions) == 0

    @pytest.mark.unit
    def test_numpy_optimization_enabled(self, detector):
        """Test that NumPy optimization is used for large value sets."""
        # Create a large set of values to trigger NumPy optimization
        mock_word = Mock()
        mock_word.text = "1.00"
        mock_word.line_id = 1

        currency_values = []
        for i in range(15):  # More than 10 values triggers NumPy
            currency_values.append(
                (
                    float(i + 1),
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text=str(i + 1),
                        extracted_value=str(i + 1),
                        metadata={},
                    ),
                )
            )

        # Add a total that's the sum of first 5 values
        total_value = sum(range(1, 6))  # 1+2+3+4+5 = 15
        currency_values.append(
            (
                float(total_value),
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text=str(total_value),
                    extracted_value=str(total_value),
                    metadata={},
                ),
            )
        )

        with patch.object(
            detector,
            "_find_solutions_numpy_optimized",
            wraps=detector._find_solutions_numpy_optimized,
        ) as mock_numpy:
            solutions = detector.solve_receipt_math(currency_values)

            # Should have called NumPy optimization
            mock_numpy.assert_called()
            assert len(solutions) > 0

    @pytest.mark.unit
    def test_numpy_vs_brute_force_consistency(self, detector, legacy_detector):
        """Test that NumPy and brute force methods produce consistent results."""
        mock_word = Mock()
        mock_word.text = "1.00"
        mock_word.line_id = 1

        # Create a moderate-sized problem
        currency_values = [
            (
                2.99,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="2.99",
                    extracted_value="2.99",
                    metadata={},
                ),
            ),
            (
                4.50,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="4.50",
                    extracted_value="4.50",
                    metadata={},
                ),
            ),
            (
                1.25,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="1.25",
                    extracted_value="1.25",
                    metadata={},
                ),
            ),
            (
                0.60,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.TAX,
                    confidence=0.9,
                    matched_text="0.60",
                    extracted_value="0.60",
                    metadata={},
                ),
            ),
            (
                8.09,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="8.09",
                    extracted_value="8.09",
                    metadata={},
                ),
            ),
        ]

        numpy_solutions = detector.solve_receipt_math(currency_values)
        brute_force_solutions = legacy_detector.solve_receipt_math(
            currency_values
        )

        # Both should find similar solutions
        assert len(numpy_solutions) > 0
        assert len(brute_force_solutions) > 0

        # Best solutions should have similar confidence
        numpy_best = numpy_solutions[0]
        brute_force_best = brute_force_solutions[0]

        assert abs(numpy_best.confidence - brute_force_best.confidence) < 0.1

    @pytest.mark.unit
    def test_tolerance_handling(self, detector):
        """Test that tolerance parameter is properly handled."""
        mock_word = Mock()
        mock_word.text = "5.99"
        mock_word.line_id = 1

        # Values that sum to 8.00, with total of 8.01 (within 0.02 tolerance)
        currency_values = [
            (
                3.00,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="3.00",
                    extracted_value="3.00",
                    metadata={},
                ),
            ),
            (
                5.00,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="5.00",
                    extracted_value="5.00",
                    metadata={},
                ),
            ),
            (
                8.01,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="8.01",
                    extracted_value="8.01",
                    metadata={},
                ),
            ),
        ]

        solutions = detector.solve_receipt_math(currency_values)

        # Should find solution despite small difference
        assert len(solutions) > 0
        best_solution = solutions[0]
        assert best_solution.confidence > 0.7

    @pytest.mark.unit
    def test_max_solutions_limit(self, detector):
        """Test that max_solutions parameter limits the number of returned solutions."""
        limited_detector = MathSolverDetector(
            tolerance=0.02, max_solutions=5, use_numpy_optimization=True
        )

        mock_word = Mock()
        mock_word.text = "1.00"
        mock_word.line_id = 1

        # Create many possible combinations
        currency_values = []
        for i in range(1, 10):
            currency_values.append(
                (
                    float(i),
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text=str(i),
                        extracted_value=str(i),
                        metadata={},
                    ),
                )
            )

        # Multiple possible totals
        for total in [10, 15, 20, 25]:
            currency_values.append(
                (
                    float(total),
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.GRAND_TOTAL,
                        confidence=0.9,
                        matched_text=str(total),
                        extracted_value=str(total),
                        metadata={},
                    ),
                )
            )

        solutions = limited_detector.solve_receipt_math(currency_values)

        # Should respect max_solutions limit
        assert len(solutions) <= 5

    @pytest.mark.unit
    def test_find_best_solution_ranking(self, detector):
        """Test that find_best_solution properly ranks solutions."""
        mock_word = Mock()
        mock_word.text = "5.99"
        mock_word.line_id = 1

        # Create multiple solutions with different characteristics
        solutions = [
            MathSolution(
                item_prices=[
                    (
                        2.99,
                        PatternMatch(
                            word=mock_word,
                            pattern_type=PatternType.CURRENCY,
                            confidence=0.9,
                            matched_text="2.99",
                            extracted_value="2.99",
                            metadata={},
                        ),
                    )
                ],
                subtotal=2.99,
                tax=None,
                grand_total=(
                    2.99,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.GRAND_TOTAL,
                        confidence=0.9,
                        matched_text="2.99",
                        extracted_value="2.99",
                        metadata={},
                    ),
                ),
                confidence=0.8,
            ),
            MathSolution(
                item_prices=[
                    (
                        2.99,
                        PatternMatch(
                            word=mock_word,
                            pattern_type=PatternType.CURRENCY,
                            confidence=0.9,
                            matched_text="2.99",
                            extracted_value="2.99",
                            metadata={},
                        ),
                    )
                ],
                subtotal=2.99,
                tax=(
                    0.30,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.TAX,
                        confidence=0.9,
                        matched_text="0.30",
                        extracted_value="0.30",
                        metadata={},
                    ),
                ),
                grand_total=(
                    3.29,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.GRAND_TOTAL,
                        confidence=0.9,
                        matched_text="3.29",
                        extracted_value="3.29",
                        metadata={},
                    ),
                ),
                confidence=0.9,
            ),
        ]

        best_solution = detector.find_best_solution(solutions)

        # Should prefer the solution with tax (higher confidence and more realistic)
        assert best_solution.tax is not None
        assert best_solution.confidence == 0.9

    @pytest.mark.unit
    def test_solution_confidence_scoring(self, detector):
        """Test that solution confidence scoring includes various factors."""
        mock_word = Mock()
        mock_word.text = "5.99"
        mock_word.line_id = 1

        # Create a solution with realistic characteristics
        solution = MathSolution(
            item_prices=[
                (
                    2.99,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text="2.99",
                        extracted_value="2.99",
                        metadata={},
                    ),
                ),
                (
                    4.50,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text="4.50",
                        extracted_value="4.50",
                        metadata={},
                    ),
                ),
                (
                    1.25,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text="1.25",
                        extracted_value="1.25",
                        metadata={},
                    ),
                ),
            ],
            subtotal=8.74,
            tax=(
                0.70,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.TAX,
                    confidence=0.9,
                    matched_text="0.70",
                    extracted_value="0.70",
                    metadata={},
                ),
            ),
            grand_total=(
                9.44,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="9.44",
                    extracted_value="9.44",
                    metadata={},
                ),
            ),
            confidence=0.9,
        )

        scored_solution = detector.find_best_solution([solution])

        # Should have higher score due to:
        # - Reasonable number of items (3)
        # - Has tax structure
        # - Realistic tax rate (8%)
        assert scored_solution.confidence >= 0.9

    @pytest.mark.unit
    def test_empty_currency_list(self, detector):
        """Test handling of empty currency values list."""
        solutions = detector.solve_receipt_math([])

        assert len(solutions) == 0

    @pytest.mark.unit
    def test_invalid_currency_values(self, detector):
        """Test handling of invalid currency values."""
        mock_word = Mock()
        mock_word.text = "invalid"
        mock_word.line_id = 1

        # Values that are too small or too large
        currency_values = [
            (
                0.001,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="0.001",
                    extracted_value="0.001",
                    metadata={},
                ),
            ),  # Too small
            (
                50000.0,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.CURRENCY,
                    confidence=0.9,
                    matched_text="50000.0",
                    extracted_value="50000.0",
                    metadata={},
                ),
            ),  # Too large
        ]

        solutions = detector.solve_receipt_math(currency_values)

        # Should handle gracefully - either no solutions or very low confidence
        if solutions:
            assert all(s.confidence < 0.5 for s in solutions)

    @pytest.mark.unit
    def test_extract_currency_values(self, detector):
        """Test extraction of currency values from pattern matches."""
        # Create separate mock words with different text values
        mock_word1 = Mock()
        mock_word1.text = "$5.99"
        mock_word1.line_id = 1

        mock_word2 = Mock()
        mock_word2.text = "$15.99"
        mock_word2.line_id = 2

        mock_word3 = Mock()
        mock_word3.text = "$2.50"
        mock_word3.line_id = 3

        pattern_matches = [
            PatternMatch(
                word=mock_word1,
                pattern_type=PatternType.CURRENCY,
                confidence=0.9,
                matched_text="$5.99",
                extracted_value="$5.99",
                metadata={},
            ),
            PatternMatch(
                word=mock_word2,
                pattern_type=PatternType.GRAND_TOTAL,
                confidence=0.9,
                matched_text="$15.99",
                extracted_value="$15.99",
                metadata={},
            ),
            PatternMatch(
                word=mock_word3,
                pattern_type=PatternType.UNIT_PRICE,
                confidence=0.9,
                matched_text="$2.50",
                extracted_value="$2.50",
                metadata={},
            ),
        ]

        currency_values = detector.extract_currency_values(pattern_matches)

        assert len(currency_values) == 3
        assert currency_values[0][0] == 5.99
        assert currency_values[1][0] == 15.99
        assert currency_values[2][0] == 2.50

    @pytest.mark.performance
    def test_numpy_optimization_performance(self, detector):
        """Test that NumPy optimization provides performance benefits for large datasets."""
        import time

        mock_word = Mock()
        mock_word.text = "1.00"
        mock_word.line_id = 1

        # Create a large dataset
        currency_values = []
        for i in range(20):
            currency_values.append(
                (
                    float(i + 1),
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text=str(i + 1),
                        extracted_value=str(i + 1),
                        metadata={},
                    ),
                )
            )

        # Add a total
        currency_values.append(
            (
                210.0,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="210.0",
                    extracted_value="210.0",
                    metadata={},
                ),
            )
        )

        # Test NumPy optimization
        start_time = time.time()
        numpy_solutions = detector.solve_receipt_math(currency_values)
        numpy_time = time.time() - start_time

        # Test brute force
        legacy_detector = MathSolverDetector(
            tolerance=0.02, max_solutions=50, use_numpy_optimization=False
        )
        start_time = time.time()
        brute_force_solutions = legacy_detector.solve_receipt_math(
            currency_values
        )
        brute_force_time = time.time() - start_time

        # NumPy should be faster (or at least not significantly slower)
        assert numpy_time <= brute_force_time * 2  # Allow some variance

        # Both should find solutions
        assert len(numpy_solutions) > 0
        assert len(brute_force_solutions) > 0


class TestMathSolution:
    """Test cases for MathSolution dataclass."""

    @pytest.mark.unit
    def test_math_solution_creation(self):
        """Test basic MathSolution creation."""
        mock_word = Mock()
        mock_word.text = "5.99"
        mock_word.line_id = 1

        solution = MathSolution(
            item_prices=[
                (
                    2.99,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text="2.99",
                        extracted_value="2.99",
                        metadata={},
                    ),
                )
            ],
            subtotal=2.99,
            tax=None,
            grand_total=(
                2.99,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="2.99",
                    extracted_value="2.99",
                    metadata={},
                ),
            ),
            confidence=0.8,
        )

        assert solution.subtotal == 2.99
        assert solution.tax is None
        assert solution.confidence == 0.8
        assert len(solution.item_prices) == 1
        assert solution.grand_total[0] == 2.99

    @pytest.mark.unit
    def test_math_solution_with_tax(self):
        """Test MathSolution with tax structure."""
        mock_word = Mock()
        mock_word.text = "5.99"
        mock_word.line_id = 1

        solution = MathSolution(
            item_prices=[
                (
                    2.99,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text="2.99",
                        extracted_value="2.99",
                        metadata={},
                    ),
                ),
                (
                    4.50,
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text="4.50",
                        extracted_value="4.50",
                        metadata={},
                    ),
                ),
            ],
            subtotal=7.49,
            tax=(
                0.60,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.TAX,
                    confidence=0.9,
                    matched_text="0.60",
                    extracted_value="0.60",
                    metadata={},
                ),
            ),
            grand_total=(
                8.09,
                PatternMatch(
                    word=mock_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="8.09",
                    extracted_value="8.09",
                    metadata={},
                ),
            ),
            confidence=0.9,
        )

        assert solution.subtotal == 7.49
        assert solution.tax[0] == 0.60
        assert solution.grand_total[0] == 8.09
        assert len(solution.item_prices) == 2
        assert solution.confidence == 0.9
