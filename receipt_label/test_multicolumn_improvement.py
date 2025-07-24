#!/usr/bin/env python3
"""
Test script to measure improvement from multicolumn handler.

This script compares line item detection with and without the multicolumn handler
to quantify the reduction in LLM dependency.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.spatial.multicolumn_handler import MultiColumnHandler, create_enhanced_line_item_detector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_dynamo.entities.receipt_word import ReceiptWord


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
    confidence: float = 0.9,
) -> ReceiptWord:
    """Helper function to create ReceiptWord objects for testing."""
    if bounding_box is None:
        bounding_box = {"x": 0.1, "y": 0.1, "width": 0.15, "height": 0.02}
    if top_left is None:
        top_left = {"x": bounding_box["x"], "y": bounding_box["y"]}
    if top_right is None:
        top_right = {"x": bounding_box["x"] + bounding_box["width"], "y": bounding_box["y"]}
    if bottom_left is None:
        bottom_left = {"x": bounding_box["x"], "y": bounding_box["y"] + bounding_box["height"]}
    if bottom_right is None:
        bottom_right = {"x": bounding_box["x"] + bounding_box["width"], "y": bounding_box["y"] + bounding_box["height"]}

    return ReceiptWord(
        receipt_id=receipt_id,
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=bounding_box,
        top_left=top_left,
        top_right=top_right,
        bottom_left=bottom_left,
        bottom_right=bottom_right,
        angle_degrees=angle_degrees,
        angle_radians=angle_radians,
        confidence=confidence,
        extracted_data={},
        embedding_status="NONE"
    )


def create_synthetic_receipt_data() -> Tuple[List[ReceiptWord], Dict[str, List[PatternMatch]]]:
    """Create synthetic multicolumn receipt data for testing."""
    words = [
        # Description column
        create_receipt_word(word_id=1, text="Apples", line_id=1, bounding_box={"x": 0.1, "y": 0.15, "width": 0.15, "height": 0.02}),
        create_receipt_word(word_id=2, text="Bread", line_id=2, bounding_box={"x": 0.1, "y": 0.2, "width": 0.12, "height": 0.02}),
        create_receipt_word(word_id=3, text="Milk", line_id=3, bounding_box={"x": 0.1, "y": 0.25, "width": 0.1, "height": 0.02}),
        
        # Quantity column
        create_receipt_word(word_id=4, text="3", line_id=1, bounding_box={"x": 0.4, "y": 0.15, "width": 0.02, "height": 0.02}),
        create_receipt_word(word_id=5, text="2", line_id=2, bounding_box={"x": 0.4, "y": 0.2, "width": 0.02, "height": 0.02}),
        create_receipt_word(word_id=6, text="1", line_id=3, bounding_box={"x": 0.4, "y": 0.25, "width": 0.02, "height": 0.02}),
        
        # Unit price column
        create_receipt_word(word_id=7, text="$1.99", line_id=1, bounding_box={"x": 0.5, "y": 0.15, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id=8, text="$2.49", line_id=2, bounding_box={"x": 0.5, "y": 0.2, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id=9, text="$3.99", line_id=3, bounding_box={"x": 0.5, "y": 0.25, "width": 0.08, "height": 0.02}),
        
        # Line total column
        create_receipt_word(word_id=10, text="$5.97", line_id=1, bounding_box={"x": 0.8, "y": 0.15, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id=11, text="$4.98", line_id=2, bounding_box={"x": 0.8, "y": 0.2, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id=12, text="$3.99", line_id=3, bounding_box={"x": 0.8, "y": 0.25, "width": 0.08, "height": 0.02}),
    ]
    
    patterns = {
        'currency': [
            # Unit prices
            PatternMatch(word=words[6], pattern_type=PatternType.CURRENCY, confidence=0.9, matched_text="$1.99", extracted_value=1.99, metadata={}),
            PatternMatch(word=words[7], pattern_type=PatternType.CURRENCY, confidence=0.9, matched_text="$2.49", extracted_value=2.49, metadata={}),
            PatternMatch(word=words[8], pattern_type=PatternType.CURRENCY, confidence=0.9, matched_text="$3.99", extracted_value=3.99, metadata={}),
            # Line totals
            PatternMatch(word=words[9], pattern_type=PatternType.CURRENCY, confidence=0.9, matched_text="$5.97", extracted_value=5.97, metadata={}),
            PatternMatch(word=words[10], pattern_type=PatternType.CURRENCY, confidence=0.9, matched_text="$4.98", extracted_value=4.98, metadata={}),
            PatternMatch(word=words[11], pattern_type=PatternType.CURRENCY, confidence=0.9, matched_text="$3.99", extracted_value=3.99, metadata={}),
        ],
        'quantity': [
            PatternMatch(word=words[3], pattern_type=PatternType.QUANTITY, confidence=0.8, matched_text="3", extracted_value=3, metadata={}),
            PatternMatch(word=words[4], pattern_type=PatternType.QUANTITY, confidence=0.8, matched_text="2", extracted_value=2, metadata={}),
            PatternMatch(word=words[5], pattern_type=PatternType.QUANTITY, confidence=0.8, matched_text="1", extracted_value=1, metadata={}),
        ]
    }
    
    return words, patterns


def load_test_receipts(data_dir: str = "./receipt_data_with_labels", limit: int = 50) -> List[Dict]:
    """Load test receipts from exported data."""
    receipts = []
    data_path = Path(data_dir)
    
    if not data_path.exists():
        print(f"❌ Data directory not found: {data_dir}")
        print("Please run export_all_receipts.py first to get test data.")
        return []
    
    json_files = list(data_path.glob("*.json"))[:limit]
    
    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
                receipts.append(data)
        except Exception as e:
            print(f"Error loading {json_file}: {e}")
    
    return receipts


def extract_patterns_from_receipt(receipt_data: Dict) -> Tuple[List[ReceiptWord], Dict[str, List[PatternMatch]]]:
    """Extract words and patterns from receipt data."""
    words = []
    patterns = defaultdict(list)
    
    # Convert receipt data to ReceiptWord objects
    for word_data in receipt_data.get('words', []):
        # Create ReceiptWord with available fields
        word = ReceiptWord(
            word_id=word_data.get('word_id', ''),
            text=word_data.get('text', ''),
            x=word_data.get('x', 0),
            y=word_data.get('y', 0),
            width=word_data.get('width', 0),
            height=word_data.get('height', 0),
            line_id=word_data.get('line_id', 0),
            bounding_box=word_data.get('bounding_box', {})
        )
        words.append(word)
        
        # Check if word has a label that indicates a pattern
        label = word_data.get('label', '')
        
        if label in ['UNIT_PRICE', 'LINE_TOTAL', 'PRICE', 'CURRENCY']:
            # Currency pattern
            pattern = PatternMatch(
                pattern_type=PatternType.CURRENCY,
                matched_text=word.text,
                confidence=0.9,
                word=word,
                metadata={'original_label': label}
            )
            patterns['currency'].append(pattern)
        
        elif label == 'QUANTITY':
            # Quantity pattern
            pattern = PatternMatch(
                pattern_type=PatternType.QUANTITY,
                matched_text=word.text,
                confidence=0.8,
                word=word,
                metadata={'original_label': label}
            )
            patterns['quantity'].append(pattern)
    
    return words, patterns


def analyze_with_basic_detector(words: List[ReceiptWord], patterns: Dict[str, List[PatternMatch]]) -> Dict:
    """Analyze receipt using basic vertical alignment detector."""
    detector = VerticalAlignmentDetector(use_enhanced_clustering=True)
    
    currency_patterns = patterns.get('currency', [])
    if not currency_patterns:
        return {
            'columns_detected': 0,
            'line_items_found': 0,
            'structured_data_extracted': False,
            'needs_llm': True
        }
    
    # Detect price columns
    columns = detector.detect_price_columns(currency_patterns)
    
    # Try to find line items
    line_items = detector.detect_line_items_with_alignment(words, patterns.get('currency', []))
    
    return {
        'columns_detected': len(columns),
        'line_items_found': len(line_items.get('line_items', [])) if isinstance(line_items, dict) else 0,
        'structured_data_extracted': len(columns) > 0,
        'needs_llm': len(columns) == 0 or not line_items
    }


def analyze_with_multicolumn_handler(words: List[ReceiptWord], patterns: Dict[str, List[PatternMatch]]) -> Dict:
    """Analyze receipt using multicolumn handler."""
    handler = create_enhanced_line_item_detector()
    
    currency_patterns = patterns.get('currency', [])
    quantity_patterns = patterns.get('quantity', [])
    
    # Detect column structure
    columns = handler.detect_column_structure(words, currency_patterns, quantity_patterns)
    
    # Assemble line items
    line_items = handler.assemble_line_items(columns, words, patterns)
    
    # Check if we have complete structured data
    has_complete_items = any(
        item.description and item.line_total is not None
        for item in line_items
    )
    
    has_validated_items = any(
        item.validation_status.get('quantity_price_total', False)
        for item in line_items
    )
    
    return {
        'columns_detected': len(columns),
        'column_types': [col.column_type.value for col in columns.values()],
        'line_items_found': len(line_items),
        'complete_items': sum(1 for item in line_items if item.description and item.line_total),
        'validated_items': sum(1 for item in line_items if item.validation_status.get('quantity_price_total', False)),
        'structured_data_extracted': has_complete_items,
        'needs_llm': not has_complete_items,
        'confidence_boost': has_validated_items
    }


def main():
    """Run comparison test."""
    print("🧪 Testing MultiColumn Handler Improvement")
    print("=" * 60)
    
    # Load test receipts
    receipts = load_test_receipts(limit=50)
    
    if not receipts:
        print("No test data found. Creating synthetic test...")
        # Create synthetic test data
        print("Creating synthetic test data...")
        words, patterns = create_synthetic_receipt_data()
        receipts = [{'words': [], 'patterns': patterns}]  # Simplified structure
        
        # Run single test
        basic_result = analyze_with_basic_detector(words, patterns)
        multi_result = analyze_with_multicolumn_handler(words, patterns)
        
        print("\n📊 Synthetic Test Results:")
        print(f"Basic Detector: {basic_result}")
        print(f"MultiColumn Handler: {multi_result}")
        return
    
    print(f"📂 Loaded {len(receipts)} receipts for testing\n")
    
    # Results tracking
    basic_results = {
        'needs_llm': 0,
        'structured_data': 0,
        'total_columns': 0,
        'total_items': 0
    }
    
    multi_results = {
        'needs_llm': 0,
        'structured_data': 0,
        'total_columns': 0,
        'total_items': 0,
        'validated_items': 0,
        'column_types': defaultdict(int)
    }
    
    # Process each receipt
    for i, receipt in enumerate(receipts):
        print(f"\rProcessing receipt {i+1}/{len(receipts)}...", end='', flush=True)
        
        words, patterns = extract_patterns_from_receipt(receipt)
        
        # Skip if no patterns found
        if not any(patterns.values()):
            continue
        
        # Analyze with basic detector
        basic = analyze_with_basic_detector(words, patterns)
        basic_results['needs_llm'] += 1 if basic['needs_llm'] else 0
        basic_results['structured_data'] += 1 if basic['structured_data_extracted'] else 0
        basic_results['total_columns'] += basic['columns_detected']
        basic_results['total_items'] += basic['line_items_found']
        
        # Analyze with multicolumn handler
        multi = analyze_with_multicolumn_handler(words, patterns)
        multi_results['needs_llm'] += 1 if multi['needs_llm'] else 0
        multi_results['structured_data'] += 1 if multi['structured_data_extracted'] else 0
        multi_results['total_columns'] += multi['columns_detected']
        multi_results['total_items'] += multi['line_items_found']
        multi_results['validated_items'] += multi.get('validated_items', 0)
        
        for col_type in multi.get('column_types', []):
            multi_results['column_types'][col_type] += 1
    
    # Calculate improvement metrics
    print("\n\n📊 Results Summary")
    print("=" * 60)
    
    receipts_with_patterns = len([r for r in receipts if any(extract_patterns_from_receipt(r)[1].values())])
    
    print(f"\n🔍 Basic Vertical Alignment Detector:")
    print(f"  - Needs LLM: {basic_results['needs_llm']}/{receipts_with_patterns} ({basic_results['needs_llm']/receipts_with_patterns*100:.1f}%)")
    print(f"  - Extracted structured data: {basic_results['structured_data']}/{receipts_with_patterns}")
    print(f"  - Average columns detected: {basic_results['total_columns']/receipts_with_patterns:.1f}")
    print(f"  - Total line items found: {basic_results['total_items']}")
    
    print(f"\n🚀 MultiColumn Handler:")
    print(f"  - Needs LLM: {multi_results['needs_llm']}/{receipts_with_patterns} ({multi_results['needs_llm']/receipts_with_patterns*100:.1f}%)")
    print(f"  - Extracted structured data: {multi_results['structured_data']}/{receipts_with_patterns}")
    print(f"  - Average columns detected: {multi_results['total_columns']/receipts_with_patterns:.1f}")
    print(f"  - Total line items found: {multi_results['total_items']}")
    print(f"  - Validated items (math verified): {multi_results['validated_items']}")
    
    print(f"\n📈 Column Types Detected:")
    for col_type, count in multi_results['column_types'].items():
        print(f"  - {col_type}: {count}")
    
    # Calculate improvement
    llm_reduction = (basic_results['needs_llm'] - multi_results['needs_llm']) / basic_results['needs_llm'] * 100
    item_increase = (multi_results['total_items'] - basic_results['total_items']) / (basic_results['total_items'] or 1) * 100
    
    print(f"\n✅ Improvement Metrics:")
    print(f"  - LLM dependency reduction: {llm_reduction:.1f}%")
    print(f"  - Line item detection increase: {item_increase:.1f}%")
    print(f"  - Mathematical validation rate: {multi_results['validated_items']/max(multi_results['total_items'], 1)*100:.1f}%")
    
    print("\n💡 Conclusion:")
    if llm_reduction > 20:
        print(f"  The MultiColumn Handler significantly reduces LLM dependency by {llm_reduction:.1f}%!")
        print("  This translates to substantial cost savings and faster processing.")
    else:
        print("  The MultiColumn Handler provides moderate improvements.")
        print("  Consider analyzing receipts with more complex multi-column layouts.")


if __name__ == "__main__":
    main()