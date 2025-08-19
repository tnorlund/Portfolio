"""
Integration tests for Phase 2 spatial/mathematical currency detection.

Tests the complete workflow combining:
- Pattern detection
- Vertical alignment detection
- Mathematical validation
- Cost reduction validation
"""

from unittest.mock import Mock

import pytest
from receipt_dynamo.entities.receipt_word import ReceiptWord

from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import (
    VerticalAlignmentDetector)


def create_receipt_word(
    receipt_id: int = 1,
    image_id: str = "12345678-1234-4567-8901-123456789012",
    line_id: int = 1,
    word_id: int = 1,
    text: str = "test",
    bounding_box: dict = None,
    top_left: dict = None,
    top_right: dict = None,
    bottom_left: dict = None,
    bottom_right: dict = None,
    angle_degrees: float = 0.0,
    angle_radians: float = 0.0,
    confidence: float = 0.9) -> ReceiptWord:
    """Helper function to create ReceiptWord objects for testing."""
    if bounding_box is None:
        bounding_box = {"x": 0.1, "y": 0.1, "width": 0.15, "height": 0.02}
    if top_left is None:
        top_left = {"x": bounding_box["x"], "y": bounding_box["y"]}
    if top_right is None:
        top_right = {
            "x": bounding_box["x"] + bounding_box["width"],
            "y": bounding_box["y"],
        }
    if bottom_left is None:
        bottom_left = {
            "x": bounding_box["x"],
            "y": bounding_box["y"] + bounding_box["height"],
        }
    if bottom_right is None:
        bottom_right = {
            "x": bounding_box["x"] + bounding_box["width"],
            "y": bounding_box["y"] + bounding_box["height"],
        }

    return ReceiptWord(
        receipt_id=receipt_id,
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=bounding_box,
        top_right=top_right,
        top_left=top_left,
        bottom_right=bottom_right,
        bottom_left=bottom_left,
        angle_degrees=angle_degrees,
        angle_radians=angle_radians,
        confidence=confidence)


class TestSpatialIntegration:
    """Integration tests for spatial/mathematical currency detection."""

    @pytest.fixture
    def math_solver(self):
        """Create a MathSolverDetector for integration testing."""
        return MathSolverDetector(
            tolerance=0.02, max_solutions=50, use_numpy_optimization=True
        )

    @pytest.fixture
    def alignment_detector(self):
        """Create a VerticalAlignmentDetector for integration testing."""
        return VerticalAlignmentDetector(
            alignment_tolerance=0.02, use_enhanced_clustering=True
        )

    @pytest.fixture
    def sample_receipt_data(self):
        """Create sample receipt data for integration testing."""
        # Create realistic receipt word layout
        words = []

        # Header section
        words.append(
            create_receipt_word(
                line_id=1,
                word_id=1,
                text="McDonald's",
                bounding_box={
                    "x": 0.3,
                    "y": 0.05,
                    "width": 0.4,
                    "height": 0.03,
                },
                confidence=0.95)
        )

        # Items section
        items = [
            ("Big Mac", 5.99, 10),
            ("Fries", 2.99, 11),
            ("Drink", 1.99, 12),
        ]

        for item_name, price, line_id in items:
            # Product name
            words.append(
                create_receipt_word(
                    receipt_id=1,
                    image_id="12345678-1234-4567-8901-123456789012",
                    line_id=line_id,
                    word_id=line_id * 10,
                    text=item_name,
                    bounding_box={
                        "x": 0.1,
                        "y": 0.1 + line_id * 0.03,
                        "width": 0.15,
                        "height": 0.02,
                    },
                    top_left={"x": 0.1, "y": 0.1 + line_id * 0.03},
                    bottom_right={"x": 0.25, "y": 0.12 + line_id * 0.03},
                    confidence=0.9)
            )

            # Price
            words.append(
                create_receipt_word(
                    receipt_id=1,
                    image_id="12345678-1234-4567-8901-123456789012",
                    line_id=line_id,
                    word_id=line_id * 10 + 1,
                    text=str(price),
                    bounding_box={
                        "x": 0.8,
                        "y": 0.1 + line_id * 0.03,
                        "width": 0.06,
                        "height": 0.02,
                    },
                    top_left={"x": 0.8, "y": 0.1 + line_id * 0.03},
                    bottom_right={"x": 0.86, "y": 0.12 + line_id * 0.03},
                    confidence=0.9)
            )

        # Totals section
        words.append(
            create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=20,
                word_id=200,
                text="Subtotal",
                bounding_box={
                    "x": 0.1,
                    "y": 0.5,
                    "width": 0.12,
                    "height": 0.02,
                },
                top_left={"x": 0.1, "y": 0.5},
                bottom_right={"x": 0.22, "y": 0.52},
                confidence=0.9)
        )

        words.append(
            create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=20,
                word_id=201,
                text="10.97",
                bounding_box={
                    "x": 0.8,
                    "y": 0.5,
                    "width": 0.06,
                    "height": 0.02,
                },
                top_left={"x": 0.8, "y": 0.5},
                bottom_right={"x": 0.86, "y": 0.52},
                confidence=0.9)
        )

        words.append(
            create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=21,
                word_id=210,
                text="Tax",
                bounding_box={
                    "x": 0.1,
                    "y": 0.53,
                    "width": 0.05,
                    "height": 0.02,
                },
                top_left={"x": 0.1, "y": 0.53},
                bottom_right={"x": 0.15, "y": 0.55},
                confidence=0.9)
        )

        words.append(
            create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=21,
                word_id=211,
                text="0.88",
                bounding_box={
                    "x": 0.8,
                    "y": 0.53,
                    "width": 0.06,
                    "height": 0.02,
                },
                top_left={"x": 0.8, "y": 0.53},
                bottom_right={"x": 0.86, "y": 0.55},
                confidence=0.9)
        )

        words.append(
            create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=22,
                word_id=220,
                text="TOTAL",
                bounding_box={
                    "x": 0.1,
                    "y": 0.58,
                    "width": 0.08,
                    "height": 0.025,
                },  # Larger font
                top_left={"x": 0.1, "y": 0.58},
                bottom_right={"x": 0.18, "y": 0.605},
                confidence=0.9)
        )

        words.append(
            create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=22,
                word_id=221,
                text="11.85",
                bounding_box={
                    "x": 0.8,
                    "y": 0.58,
                    "width": 0.06,
                    "height": 0.025,
                },  # Larger font
                top_left={"x": 0.8, "y": 0.58},
                bottom_right={"x": 0.86, "y": 0.605},
                confidence=0.9)
        )

        return words

    @pytest.fixture
    def sample_pattern_matches(self, sample_receipt_data):
        """Create sample pattern matches for integration testing."""
        patterns = []

        # Find currency words and create pattern matches
        currency_words = [
            w
            for w in sample_receipt_data
            if w.text.replace(".", "").isdigit() and "." in w.text
        ]

        for word in currency_words:
            if word.text == "11.85":
                pattern_type = PatternType.GRAND_TOTAL
            elif word.text == "10.97":
                pattern_type = PatternType.SUBTOTAL
            elif word.text == "0.88":
                pattern_type = PatternType.TAX
            else:
                pattern_type = PatternType.CURRENCY

            patterns.append(
                PatternMatch(
                    word=word,
                    pattern_type=pattern_type,
                    confidence=0.9,
                    matched_text=word.text,
                    extracted_value=word.text,
                    metadata={})
            )

        return patterns

    @pytest.mark.integration
    def test_complete_spatial_detection_workflow(
        self,
        math_solver,
        alignment_detector,
        sample_receipt_data,
        sample_pattern_matches):
        """Test complete spatial detection workflow."""
        # Step 1: Detect price columns using vertical alignment
        currency_patterns = [
            p
            for p in sample_pattern_matches
            if p.pattern_type
            in [
                PatternType.CURRENCY,
                PatternType.GRAND_TOTAL,
                PatternType.SUBTOTAL,
                PatternType.TAX,
            ]
        ]

        price_columns = alignment_detector.detect_price_columns(
            currency_patterns
        )

        assert len(price_columns) >= 1
        assert price_columns[0].confidence > 0.5

        # Step 2: Extract currency values for mathematical validation
        currency_values = []
        for pattern in currency_patterns:
            try:
                value = float(pattern.extracted_value)
                if 0.01 <= value <= 999.99:
                    currency_values.append((value, pattern))
            except ValueError:
                continue

        assert (
            len(currency_values) >= 4
        )  # Should have items + subtotal + tax + total

        # Step 3: Solve mathematical relationships
        solutions = math_solver.solve_receipt_math(currency_values)

        assert len(solutions) > 0

        # Should find the correct solution: 5.99 + 2.99 + 1.99 + 0.88 = 11.85
        best_solution = solutions[0]
        assert best_solution.confidence > 0.8
        assert best_solution.tax is not None  # Should detect tax structure
        assert abs(best_solution.tax[0] - 0.88) < 0.01  # Tax should be 0.88

        # Step 4: Match products to prices using alignment
        if price_columns:
            line_items = alignment_detector.match_products_to_column(
                sample_receipt_data, price_columns[0]
            )

            assert len(line_items) >= 3  # Should match 3 products

            # Should find product matches
            product_texts = [item.product_text for item in line_items]
            assert any("Big Mac" in text for text in product_texts)
            assert any("Fries" in text for text in product_texts)
            assert any("Drink" in text for text in product_texts)

    @pytest.mark.integration
    def test_spatial_with_mathematical_validation(
        self,
        math_solver,
        alignment_detector,
        sample_receipt_data,
        sample_pattern_matches):
        """Test spatial detection combined with mathematical validation."""
        # Get spatial analysis results
        spatial_result = alignment_detector.detect_line_items_with_alignment(
            sample_receipt_data, sample_pattern_matches
        )

        # Extract currency values from spatial results
        currency_values = []
        for pattern in sample_pattern_matches:
            if pattern.pattern_type in [
                PatternType.CURRENCY,
                PatternType.GRAND_TOTAL,
                PatternType.SUBTOTAL,
                PatternType.TAX,
            ]:
                try:
                    value = float(pattern.extracted_value)
                    currency_values.append((value, pattern))
                except ValueError:
                    continue

        # Validate mathematically
        solutions = math_solver.solve_receipt_math(currency_values)

        # Combined spatial + mathematical should give high confidence
        assert spatial_result["best_column_confidence"] > 0.5
        assert len(solutions) > 0
        assert solutions[0].confidence > 0.8

        # Should achieve Phase 2 quality metrics
        if "x_alignment_tightness" in spatial_result:
            assert spatial_result["x_alignment_tightness"] >= 0.0
        if "font_consistency_confidence" in spatial_result:
            assert spatial_result["font_consistency_confidence"] >= 0.0

    @pytest.mark.integration
    def test_phase2_feature_integration(
        self, alignment_detector, sample_receipt_data, sample_pattern_matches
    ):
        """Test Phase 2 feature integration."""
        # Test enhanced clustering features
        result = alignment_detector.detect_line_items_with_alignment(
            sample_receipt_data, sample_pattern_matches
        )

        # Should have Phase 2 metadata
        assert "spatial_analysis_version" in result
        assert result["spatial_analysis_version"] == "enhanced_v2"

        # Should have enhanced spatial metrics
        assert "x_alignment_tightness" in result
        assert "font_consistency_confidence" in result
        assert "has_large_font_patterns" in result

        # Test price column detection with enhanced features
        currency_patterns = [
            p
            for p in sample_pattern_matches
            if p.pattern_type
            in [
                PatternType.CURRENCY,
                PatternType.GRAND_TOTAL,
                PatternType.SUBTOTAL,
                PatternType.TAX,
            ]
        ]

        price_columns = alignment_detector.detect_price_columns(
            currency_patterns
        )

        if price_columns:
            best_column = price_columns[0]

            # Should have Phase 2 features
            assert hasattr(best_column, "x_alignment_tightness")
            assert hasattr(best_column, "font_consistency")
            assert hasattr(best_column, "y_span")

            # Values should be calculated
            assert best_column.x_alignment_tightness is not None
            if best_column.font_consistency:
                assert best_column.font_consistency.confidence >= 0.0

    @pytest.mark.integration
    def test_cost_reduction_validation(
        self,
        math_solver,
        alignment_detector,
        sample_receipt_data,
        sample_pattern_matches):
        """Test that the system achieves cost reduction goals."""

        # Simulate the simplified confidence classification
        def classify_confidence_simplified(solutions, spatial_analysis):
            """Simplified confidence classification from test_simplified_confidence.py"""
            if not solutions:
                return "no_solution"

            best_solution = max(solutions, key=lambda s: s.confidence)

            # Extract spatial metrics
            column_confidence = spatial_analysis.get(
                "best_column_confidence", 0
            )
            x_tightness = spatial_analysis.get("x_alignment_tightness", 0)
            has_large_fonts = spatial_analysis.get(
                "has_large_font_patterns", False
            )
            font_consistency = spatial_analysis.get(
                "font_consistency_confidence", 0
            )

            # Calculate combined score
            math_score = best_solution.confidence
            spatial_score = column_confidence

            # Phase 2 bonuses
            if x_tightness > 0.9:
                spatial_score *= 1.1  # Tight alignment bonus
            if has_large_fonts:
                spatial_score *= 1.1  # Large font detection bonus
            if font_consistency > 0.6:
                spatial_score *= 1.05  # Font consistency bonus

            # Combine scores
            combined_score = (math_score + spatial_score) / 2

            # Simplified classification
            if combined_score >= 0.85:
                return "high_confidence"
            elif combined_score >= 0.7:
                return "medium_confidence"
            elif combined_score >= 0.5:
                return "low_confidence"
            else:
                return "no_solution"

        # Get spatial analysis
        spatial_result = alignment_detector.detect_line_items_with_alignment(
            sample_receipt_data, sample_pattern_matches
        )

        # Extract currency values
        currency_values = []
        for pattern in sample_pattern_matches:
            if pattern.pattern_type in [
                PatternType.CURRENCY,
                PatternType.GRAND_TOTAL,
                PatternType.SUBTOTAL,
                PatternType.TAX,
            ]:
                try:
                    value = float(pattern.extracted_value)
                    currency_values.append((value, pattern))
                except ValueError:
                    continue

        # Get mathematical solutions
        solutions = math_solver.solve_receipt_math(currency_values)

        # Classify confidence
        confidence = classify_confidence_simplified(solutions, spatial_result)

        # Should achieve high or medium confidence (contributing to cost reduction)
        assert confidence in ["high_confidence", "medium_confidence"]

        # High confidence means no GPT needed (100% cost reduction for this receipt)
        # Medium confidence means light GPT usage (70% cost reduction for this receipt)
        if confidence == "high_confidence":
            cost_reduction_contribution = 1.0  # 100% for this receipt
        elif confidence == "medium_confidence":
            cost_reduction_contribution = 0.7  # 70% for this receipt
        else:
            cost_reduction_contribution = 0.0  # 0% for this receipt

        # Should contribute to overall cost reduction goal
        assert cost_reduction_contribution >= 0.7

    @pytest.mark.integration
    def test_numpy_optimization_integration(
        self, sample_receipt_data, sample_pattern_matches
    ):
        """Test NumPy optimization integration with spatial detection."""
        # Create large dataset to trigger NumPy optimization
        large_currency_values = []

        for i in range(15):  # More than 10 values
            mock_word = Mock()
            mock_word.text = f"{i+1}.99"
            mock_word.line_id = i + 1

            large_currency_values.append(
                (
                    float(i + 1),
                    PatternMatch(
                        word=mock_word,
                        pattern_type=PatternType.CURRENCY,
                        confidence=0.9,
                        matched_text=f"{i+1}.99",
                        extracted_value=f"{i+1}.99",
                        metadata={}))
            )

        # Add a realistic total
        mock_total_word = Mock()
        mock_total_word.text = "78.00"
        mock_total_word.line_id = 20

        large_currency_values.append(
            (
                78.0,
                PatternMatch(
                    word=mock_total_word,
                    pattern_type=PatternType.GRAND_TOTAL,
                    confidence=0.9,
                    matched_text="78.00",
                    extracted_value="78.00",
                    metadata={}))
        )

        # Test both optimized and non-optimized
        optimized_solver = MathSolverDetector(
            tolerance=0.02, max_solutions=50, use_numpy_optimization=True
        )
        legacy_solver = MathSolverDetector(
            tolerance=0.02, max_solutions=50, use_numpy_optimization=False
        )

        optimized_solutions = optimized_solver.solve_receipt_math(
            large_currency_values
        )
        legacy_solutions = legacy_solver.solve_receipt_math(
            large_currency_values
        )

        # Both should find solutions
        assert len(optimized_solutions) > 0
        assert len(legacy_solutions) > 0

        # Optimized should be at least as good as legacy
        if optimized_solutions and legacy_solutions:
            optimized_best = optimized_solutions[0]
            legacy_best = legacy_solutions[0]

            # Should have similar or better confidence
            assert optimized_best.confidence >= legacy_best.confidence - 0.1

    @pytest.mark.performance
    def test_spatial_integration_performance(
        self,
        math_solver,
        alignment_detector,
        sample_receipt_data,
        sample_pattern_matches):
        """Test performance of integrated spatial detection."""
        import time

        # Measure complete workflow performance
        start_time = time.time()

        # Full spatial + mathematical detection
        spatial_result = alignment_detector.detect_line_items_with_alignment(
            sample_receipt_data, sample_pattern_matches
        )

        currency_values = []
        for pattern in sample_pattern_matches:
            if pattern.pattern_type in [
                PatternType.CURRENCY,
                PatternType.GRAND_TOTAL,
                PatternType.SUBTOTAL,
                PatternType.TAX,
            ]:
                try:
                    value = float(pattern.extracted_value)
                    currency_values.append((value, pattern))
                except ValueError:
                    continue

        solutions = math_solver.solve_receipt_math(currency_values)

        total_time = time.time() - start_time

        # Should complete within reasonable time
        assert total_time < 1.0  # Should complete in under 1 second

        # Should produce results
        assert spatial_result["total_items"] > 0
        assert len(solutions) > 0
