"""
Test currency parsing fixes for baseline_metrics.py and test_negative_space_integration.py

This test verifies that currency values with symbols and commas are parsed correctly
without causing ValueError exceptions.
"""

import json
import pytest
from pathlib import Path
import tempfile
import sys

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import from the actual module paths
from line_item_enhancement.evaluation.baseline_metrics import load_receipt_data
from test_negative_space_integration import load_receipt_data as load_receipt_data_integration


class TestCurrencyParsing:
    """Test currency parsing handles symbols and commas correctly"""
    
    @pytest.fixture
    def temp_receipt_with_currency_symbols(self):
        """Create a temporary receipt file with currency symbols"""
        receipt_data = {
            "words": [
                {
                    "text": "$12.99",
                    "line_id": 0,
                    "word_id": 0,
                    "confidence": 0.95,
                    "bounding_box": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.02}
                },
                {
                    "text": "$1,234.56",
                    "line_id": 1,
                    "word_id": 1,
                    "confidence": 0.95,
                    "bounding_box": {"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02}
                },
                {
                    "text": "9.99",  # No currency symbol
                    "line_id": 2,
                    "word_id": 2,
                    "confidence": 0.95,
                    "bounding_box": {"x": 0.1, "y": 0.3, "width": 0.1, "height": 0.02}
                },
                {
                    "text": "invalid$price",  # Invalid format
                    "line_id": 3,
                    "word_id": 3,
                    "confidence": 0.95,
                    "bounding_box": {"x": 0.1, "y": 0.4, "width": 0.1, "height": 0.02}
                }
            ],
            "pattern_matches": [
                {
                    "pattern_type": "CURRENCY",
                    "matched_text": "$12.99",
                    "confidence": 0.9
                },
                {
                    "pattern_type": "CURRENCY",
                    "matched_text": "$1,234.56",
                    "confidence": 0.9
                },
                {
                    "pattern_type": "CURRENCY",
                    "matched_text": "9.99",
                    "confidence": 0.9
                },
                {
                    "pattern_type": "CURRENCY",
                    "matched_text": "invalid$price",  # This should be skipped
                    "confidence": 0.9
                }
            ]
        }
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(receipt_data, f)
            temp_path = Path(f.name)
        
        yield temp_path
        
        # Cleanup
        temp_path.unlink()
    
    def test_baseline_metrics_currency_parsing(self, temp_receipt_with_currency_symbols):
        """Test baseline_metrics.py handles currency symbols correctly"""
        # This should not raise ValueError
        words, pattern_matches, receipt_id = load_receipt_data(temp_receipt_with_currency_symbols)
        
        # Should have successfully parsed 3 valid currency values
        currency_matches = [m for m in pattern_matches if m.pattern_type.name == 'CURRENCY']
        assert len(currency_matches) == 3, f"Expected 3 currency matches, got {len(currency_matches)}"
        
        # Check extracted values
        values = [m.extracted_value for m in currency_matches]
        assert 12.99 in values, "Should parse $12.99 correctly"
        assert 1234.56 in values, "Should parse $1,234.56 correctly"
        assert 9.99 in values, "Should parse 9.99 correctly"
        
        # Check that invalid format was skipped
        matched_texts = [m.matched_text for m in currency_matches]
        assert "invalid$price" not in matched_texts, "Should skip invalid currency format"
    
    def test_integration_currency_parsing(self, temp_receipt_with_currency_symbols):
        """Test test_negative_space_integration.py handles currency symbols correctly"""
        # This should not raise ValueError
        words, pattern_matches = load_receipt_data_integration(temp_receipt_with_currency_symbols)
        
        # Should have successfully parsed 3 valid currency values
        currency_matches = [m for m in pattern_matches if m.pattern_type.name == 'CURRENCY']
        assert len(currency_matches) == 3, f"Expected 3 currency matches, got {len(currency_matches)}"
        
        # Check extracted values
        values = [m.extracted_value for m in currency_matches]
        assert 12.99 in values, "Should parse $12.99 correctly"
        assert 1234.56 in values, "Should parse $1,234.56 correctly"
        assert 9.99 in values, "Should parse 9.99 correctly"
        
        # Check that invalid format was skipped
        matched_texts = [m.matched_text for m in currency_matches]
        assert "invalid$price" not in matched_texts, "Should skip invalid currency format"
    
    def test_edge_cases(self, temp_receipt_with_currency_symbols):
        """Test edge cases in currency parsing"""
        # Create test data with edge cases
        edge_case_data = {
            "words": [
                {"text": "$", "line_id": 0, "word_id": 0, "confidence": 0.95, 
                 "bounding_box": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.02}},
                {"text": "$.99", "line_id": 1, "word_id": 1, "confidence": 0.95,
                 "bounding_box": {"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02}},
                {"text": "$0", "line_id": 2, "word_id": 2, "confidence": 0.95,
                 "bounding_box": {"x": 0.1, "y": 0.3, "width": 0.1, "height": 0.02}},
                {"text": "   $10.00   ", "line_id": 3, "word_id": 3, "confidence": 0.95,
                 "bounding_box": {"x": 0.1, "y": 0.4, "width": 0.1, "height": 0.02}}
            ],
            "pattern_matches": [
                {"pattern_type": "CURRENCY", "matched_text": "$", "confidence": 0.9},
                {"pattern_type": "CURRENCY", "matched_text": "$.99", "confidence": 0.9},
                {"pattern_type": "CURRENCY", "matched_text": "$0", "confidence": 0.9},
                {"pattern_type": "CURRENCY", "matched_text": "   $10.00   ", "confidence": 0.9}
            ]
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(edge_case_data, f)
            edge_path = Path(f.name)
        
        try:
            # Test baseline_metrics
            words, pattern_matches, _ = load_receipt_data(edge_path)
            currency_matches = [m for m in pattern_matches if m.pattern_type.name == 'CURRENCY']
            
            # Check parsed values
            values = [m.extracted_value for m in currency_matches]
            assert 0.99 in values, "Should parse $.99 as 0.99"
            assert 0 in values, "Should parse $0 as 0"
            assert 10.00 in values, "Should parse padded $10.00 correctly"
            
            # "$" alone should be skipped
            matched_texts = [m.matched_text for m in currency_matches]
            assert "$" not in [m for m in matched_texts if m == "$"], "Should skip lone $ symbol"
            
        finally:
            edge_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])