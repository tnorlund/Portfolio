"""
Tests for the enhanced pattern analyzer.
"""

import pytest
from decimal import Decimal

from receipt_label.pattern_detection.enhanced_pattern_analyzer import (
    EnhancedPatternAnalyzer,
    QuantityPatternMatcher,
    SpatialAnalyzer,
    enhanced_pattern_analysis,
)


class TestQuantityPatternMatcher:
    """Test quantity pattern matching."""
    
    def test_at_symbol_pattern(self):
        """Test quantity @ price pattern."""
        matcher = QuantityPatternMatcher()
        
        # Test basic pattern
        result = matcher.find_quantity_patterns("2 @ $5.99")
        assert result is not None
        assert result['type'] == 'at_symbol'
        assert result['quantity'] == Decimal('2')
        assert result['unit_price'] == Decimal('5.99')
        
        # Test without dollar sign
        result = matcher.find_quantity_patterns("3 @ 4.50")
        assert result is not None
        assert result['quantity'] == Decimal('3')
        assert result['unit_price'] == Decimal('4.50')
        
        # Test with decimal quantity
        result = matcher.find_quantity_patterns("2.5 @ $10.00")
        assert result is not None
        assert result['quantity'] == Decimal('2.5')
        assert result['unit_price'] == Decimal('10.00')
    
    def test_x_symbol_pattern(self):
        """Test quantity x price pattern."""
        matcher = QuantityPatternMatcher()
        
        # Test with lowercase x
        result = matcher.find_quantity_patterns("4 x $3.99")
        assert result is not None
        assert result['type'] == 'x_symbol'
        assert result['quantity'] == Decimal('4')
        assert result['unit_price'] == Decimal('3.99')
        
        # Test with uppercase X
        result = matcher.find_quantity_patterns("2 X 7.50")
        assert result is not None
        assert result['quantity'] == Decimal('2')
        assert result['unit_price'] == Decimal('7.50')
    
    def test_qty_prefix_pattern(self):
        """Test Qty: pattern."""
        matcher = QuantityPatternMatcher()
        
        # Test with colon
        result = matcher.find_quantity_patterns("Qty: 5")
        assert result is not None
        assert result['type'] == 'qty_prefix'
        assert result['quantity'] == Decimal('5')
        
        # Test without colon
        result = matcher.find_quantity_patterns("QTY 3")
        assert result is not None
        assert result['quantity'] == Decimal('3')
        
        # Test lowercase
        result = matcher.find_quantity_patterns("quantity: 2.5")
        assert result is not None
        assert result['quantity'] == Decimal('2.5')
    
    def test_weight_volume_pattern(self):
        """Test weight and volume patterns."""
        matcher = QuantityPatternMatcher()
        
        # Test pounds
        result = matcher.find_quantity_patterns("2.5 lb")
        assert result is not None
        assert result['type'] == 'weight_volume'
        assert result['quantity'] == Decimal('2.5')
        assert result['unit'] == 'lb'
        
        # Test kilograms
        result = matcher.find_quantity_patterns("3 kg")
        assert result is not None
        assert result['quantity'] == Decimal('3')
        assert result['unit'] == 'kg'
        
        # Test liters
        result = matcher.find_quantity_patterns("1.5 liters")
        assert result is not None
        assert result['quantity'] == Decimal('1.5')
        assert result['unit'] == 'liters'
    
    def test_count_unit_pattern(self):
        """Test count unit patterns."""
        matcher = QuantityPatternMatcher()
        
        # Test EA (each)
        result = matcher.find_quantity_patterns("6 EA")
        assert result is not None
        assert result['type'] == 'count_unit'
        assert result['quantity'] == Decimal('6')
        assert result['unit'] == 'EA'
        
        # Test pieces
        result = matcher.find_quantity_patterns("12 pieces")
        assert result is not None
        assert result['quantity'] == Decimal('12')
        assert result['unit'] == 'pieces'
    
    def test_no_pattern(self):
        """Test when no pattern matches."""
        matcher = QuantityPatternMatcher()
        
        result = matcher.find_quantity_patterns("just some random text")
        assert result is None
        
        result = matcher.find_quantity_patterns("$5.99")  # Price without quantity
        assert result is None


class TestSpatialAnalyzer:
    """Test spatial analysis functions."""
    
    def test_analyze_line_position(self):
        """Test line position calculation."""
        # Top of receipt
        assert SpatialAnalyzer.analyze_line_position(0, 1000) == 0.0
        
        # Middle of receipt
        assert SpatialAnalyzer.analyze_line_position(500, 1000) == 0.5
        
        # Bottom of receipt
        assert SpatialAnalyzer.analyze_line_position(1000, 1000) == 1.0
        
        # Edge cases
        assert SpatialAnalyzer.analyze_line_position(0, 0) == 0.5  # No height
        assert SpatialAnalyzer.analyze_line_position(1500, 1000) == 1.0  # Beyond height
        assert SpatialAnalyzer.analyze_line_position(-100, 1000) == 0.0  # Negative position
    
    def test_find_nearby_keywords(self):
        """Test keyword detection."""
        # Test financial keywords
        keywords, scores = SpatialAnalyzer.find_nearby_keywords("SUBTOTAL")
        assert 'subtotal' in keywords
        assert scores['subtotal'] == 1.0
        
        keywords, scores = SpatialAnalyzer.find_nearby_keywords("Sales Tax")
        assert 'tax' in keywords
        assert scores['tax'] == 1.0
        
        keywords, scores = SpatialAnalyzer.find_nearby_keywords("TOTAL DUE")
        assert 'total' in keywords
        assert scores['total'] >= 1.0  # Multiple keywords
        
        # Test item keywords
        keywords, scores = SpatialAnalyzer.find_nearby_keywords("2 @ $5.99")
        assert 'item' in keywords
        assert scores['item'] > 0
        
        # Test multiple keywords
        keywords, scores = SpatialAnalyzer.find_nearby_keywords(
            "Subtotal before tax", 
            "Additional text with total"
        )
        assert 'subtotal' in keywords
        assert 'tax' in keywords
        assert 'total' in keywords
        
        # Test no keywords
        keywords, scores = SpatialAnalyzer.find_nearby_keywords("random words here")
        assert len(keywords) == 0


class TestEnhancedPatternAnalyzer:
    """Test the main enhanced pattern analyzer."""
    
    def test_empty_contexts(self):
        """Test with empty currency contexts."""
        analyzer = EnhancedPatternAnalyzer()
        result = analyzer.analyze_currency_contexts([])
        
        assert result['line_items'] == []
        assert result['financial_summary']['subtotal'] is None
        assert result['financial_summary']['tax'] is None
        assert result['financial_summary']['total'] is None
        assert result['confidence'] == 0.0
    
    def test_simple_receipt(self):
        """Test with a simple receipt."""
        analyzer = EnhancedPatternAnalyzer()
        
        contexts = [
            {
                'amount': 5.99,
                'text': '$5.99',
                'line_id': '1',
                'x_position': 200,
                'y_position': 100,
                'full_line': 'Apple 2 @ $5.99',
                'left_text': 'Apple 2 @'
            },
            {
                'amount': 3.49,
                'text': '$3.49',
                'line_id': '2',
                'x_position': 200,
                'y_position': 150,
                'full_line': 'Banana $3.49',
                'left_text': 'Banana'
            },
            {
                'amount': 9.48,
                'text': '$9.48',
                'line_id': '3',
                'x_position': 200,
                'y_position': 300,
                'full_line': 'SUBTOTAL $9.48',
                'left_text': 'SUBTOTAL'
            },
            {
                'amount': 0.76,
                'text': '$0.76',
                'line_id': '4',
                'x_position': 200,
                'y_position': 350,
                'full_line': 'TAX $0.76',
                'left_text': 'TAX'
            },
            {
                'amount': 10.24,
                'text': '$10.24',
                'line_id': '5',
                'x_position': 200,
                'y_position': 400,
                'full_line': 'TOTAL $10.24',
                'left_text': 'TOTAL'
            }
        ]
        
        result = analyzer.analyze_currency_contexts(contexts, receipt_height=500)
        
        # Check line items
        assert len(result['line_items']) == 2
        assert result['line_items'][0]['description'] == 'Apple 2 @'
        assert result['line_items'][0]['amount'] == 5.99
        assert result['line_items'][0]['quantity'] == 2.0
        
        assert result['line_items'][1]['description'] == 'Banana'
        assert result['line_items'][1]['amount'] == 3.49
        
        # Check financial summary
        assert result['financial_summary']['subtotal'] == 9.48
        assert result['financial_summary']['tax'] == 0.76
        assert result['financial_summary']['total'] == 10.24
        
        # Check confidence
        assert result['confidence'] > 0.7
    
    def test_classification_categories(self):
        """Test classification of different categories."""
        analyzer = EnhancedPatternAnalyzer()
        
        contexts = [
            {
                'amount': 15.00,
                'text': '$15.00',
                'line_id': '1',
                'x_position': 200,
                'y_position': 100,
                'full_line': 'Item $15.00',
                'left_text': 'Item'
            },
            {
                'amount': 2.00,
                'text': '$2.00',
                'line_id': '2',
                'x_position': 200,
                'y_position': 200,
                'full_line': 'Discount -$2.00',
                'left_text': 'Discount'
            },
            {
                'amount': 1.50,
                'text': '$1.50',
                'line_id': '3',
                'x_position': 200,
                'y_position': 250,
                'full_line': 'Delivery Fee $1.50',
                'left_text': 'Delivery Fee'
            }
        ]
        
        result = analyzer.analyze_currency_contexts(contexts, receipt_height=400)
        
        # Check classifications
        classifications = result['classification']
        assert len(classifications) == 3
        
        # Find each category
        categories = {c['category']: c for c in classifications}
        assert 'LINE_ITEM' in categories
        assert 'DISCOUNT' in categories
        assert 'FEE' in categories
    
    def test_quantity_extraction(self):
        """Test extraction of quantities from patterns."""
        analyzer = EnhancedPatternAnalyzer()
        
        contexts = [
            {
                'amount': 11.98,
                'text': '$11.98',
                'line_id': '1',
                'x_position': 200,
                'y_position': 100,
                'full_line': 'Apples 2 @ $5.99 $11.98',
                'left_text': 'Apples 2 @ $5.99'
            },
            {
                'amount': 15.00,
                'text': '$15.00',
                'line_id': '2',
                'x_position': 200,
                'y_position': 150,
                'full_line': 'Ground Beef 3 lb $15.00',
                'left_text': 'Ground Beef 3 lb'
            }
        ]
        
        result = analyzer.analyze_currency_contexts(contexts, receipt_height=300)
        
        # Check line items with quantities
        items = result['line_items']
        assert len(items) >= 1
        
        # Find the apples item
        apple_item = next((i for i in items if 'Apple' in i['description']), None)
        if apple_item:
            assert apple_item['quantity'] == 2.0
            assert apple_item['unit_price'] == 5.99
        
        # Find the beef item
        beef_item = next((i for i in items if 'Beef' in i['description']), None)
        if beef_item:
            assert beef_item['quantity'] == 3.0
            assert beef_item['unit'] == 'lb'


class TestEnhancedPatternAnalysisFunction:
    """Test the main entry point function."""
    
    def test_enhanced_pattern_analysis(self):
        """Test the enhanced_pattern_analysis function."""
        contexts = [
            {
                'amount': 25.00,
                'text': '$25.00',
                'line_id': '1',
                'x_position': 200,
                'y_position': 400,
                'full_line': 'TOTAL DUE $25.00',
                'left_text': 'TOTAL DUE'
            }
        ]
        
        result = enhanced_pattern_analysis(contexts)
        
        assert 'line_items' in result
        assert 'financial_summary' in result
        assert 'classification' in result
        assert 'confidence' in result
        
        # Should identify as total
        assert result['financial_summary']['total'] == 25.00