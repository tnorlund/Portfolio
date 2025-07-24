"""
Integration test script for comparing negative space detection with existing approaches.

This script tests the negative space detector against local receipt data and
compares its performance with the existing vertical alignment detector.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import List, Dict, Tuple
from collections import defaultdict

# Import existing components
from receipt_label.models.receipt import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.negative_space_detector import NegativeSpaceDetector


def load_receipt_data(file_path: Path) -> Tuple[List[ReceiptWord], List[PatternMatch]]:
    """Load receipt words and pattern matches from JSON file"""
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    # Convert to ReceiptWord objects
    words = []
    for i, word_data in enumerate(data.get('words', [])):
        word = ReceiptWord(
            text=word_data.get('text', ''),
            line_id=word_data.get('line_id', 0),
            word_id=word_data.get('word_id', i),
            confidence=word_data.get('confidence', 0.0),
            bounding_box=word_data.get('bounding_box', {}),
            receipt_id=1,
            image_id=word_data.get('image_id', 'test_image')
        )
        words.append(word)
    
    # Extract pattern matches if available
    pattern_matches = []
    for match_data in data.get('pattern_matches', []):
        # Find the word that matches
        matched_text = match_data.get('matched_text', '')
        matching_word = next((w for w in words if w.text == matched_text), words[0] if words else None)
        
        if matching_word:
            match = PatternMatch(
                word=matching_word,
                pattern_type=PatternType.CURRENCY if match_data.get('pattern_type', '') == 'CURRENCY' else PatternType.PRODUCT_NAME,
                matched_text=matched_text,
                confidence=match_data.get('confidence', 0.0),
                extracted_value=float(matched_text) if match_data.get('pattern_type', '') == 'CURRENCY' else matched_text,
                metadata=match_data.get('metadata', {})
            )
            pattern_matches.append(match)
    
    return words, pattern_matches


def analyze_negative_space_performance(words: List[ReceiptWord], pattern_matches: List[PatternMatch]) -> Dict:
    """Analyze performance of negative space detection"""
    detector = NegativeSpaceDetector()
    
    start_time = time.time()
    
    # Detect whitespace regions
    whitespace_regions = detector.detect_whitespace_regions(words)
    
    # Detect line item boundaries
    boundaries = detector.detect_line_item_boundaries(words, whitespace_regions, pattern_matches)
    
    # Detect column structure
    column_structure = detector.detect_column_structure(words)
    
    # Enhance existing matches
    enhanced_matches = detector.enhance_line_items_with_negative_space(words, pattern_matches)
    
    end_time = time.time()
    
    # Calculate metrics
    new_line_items = [m for m in enhanced_matches if m.pattern_type == "LINE_ITEM" and m not in pattern_matches]
    
    return {
        'processing_time': end_time - start_time,
        'whitespace_regions_found': len(whitespace_regions),
        'line_item_boundaries_found': len(boundaries),
        'has_column_structure': column_structure is not None,
        'column_count': len(column_structure.columns) if column_structure else 0,
        'new_line_items_detected': len(new_line_items),
        'total_enhanced_matches': len(enhanced_matches),
        'vertical_gaps': len([r for r in whitespace_regions if r.region_type == 'vertical_gap']),
        'section_breaks': len([r for r in whitespace_regions if r.region_type == 'section_break']),
        'horizontal_gaps': len([r for r in whitespace_regions if r.region_type == 'horizontal_gap']),
        'column_channels': len([r for r in whitespace_regions if r.region_type == 'column_channel']),
    }


def analyze_vertical_alignment_performance(words: List[ReceiptWord], pattern_matches: List[PatternMatch]) -> Dict:
    """Analyze performance of existing vertical alignment detection"""
    detector = VerticalAlignmentDetector(use_enhanced_clustering=True)
    
    start_time = time.time()
    
    # Run detection
    result = detector.detect_line_items_with_alignment(words, pattern_matches)
    
    end_time = time.time()
    
    return {
        'processing_time': end_time - start_time,
        'line_items_found': len(result.get('line_items', [])),
        'best_column_confidence': result.get('best_column_confidence', 0.0),
        'column_count': len(result.get('price_columns', [])),
        'clustering_used': result.get('clustering_used', False)
    }


def compare_approaches(receipt_path: Path) -> Dict:
    """Compare negative space and vertical alignment approaches"""
    print(f"\nAnalyzing receipt: {receipt_path.name}")
    print("-" * 60)
    
    # Load data
    words, pattern_matches = load_receipt_data(receipt_path)
    print(f"Loaded {len(words)} words, {len(pattern_matches)} pattern matches")
    
    # Analyze with negative space
    ns_results = analyze_negative_space_performance(words, pattern_matches)
    print(f"\nNegative Space Detection:")
    print(f"  Processing time: {ns_results['processing_time']*1000:.2f}ms")
    print(f"  Whitespace regions: {ns_results['whitespace_regions_found']}")
    print(f"    - Vertical gaps: {ns_results['vertical_gaps']}")
    print(f"    - Section breaks: {ns_results['section_breaks']}")
    print(f"    - Horizontal gaps: {ns_results['horizontal_gaps']}")
    print(f"    - Column channels: {ns_results['column_channels']}")
    print(f"  Line item boundaries: {ns_results['line_item_boundaries_found']}")
    print(f"  Column structure: {'Yes' if ns_results['has_column_structure'] else 'No'} ({ns_results['column_count']} columns)")
    print(f"  New line items detected: {ns_results['new_line_items_detected']}")
    
    # Analyze with vertical alignment
    va_results = analyze_vertical_alignment_performance(words, pattern_matches)
    print(f"\nVertical Alignment Detection:")
    print(f"  Processing time: {va_results['processing_time']*1000:.2f}ms")
    print(f"  Line items found: {va_results['line_items_found']}")
    print(f"  Best column confidence: {va_results['best_column_confidence']:.2f}")
    print(f"  Columns detected: {va_results['column_count']}")
    
    # Calculate improvement
    speedup = va_results['processing_time'] / ns_results['processing_time'] if ns_results['processing_time'] > 0 else 0
    additional_items = ns_results['new_line_items_detected']
    
    print(f"\nComparison:")
    print(f"  Speed improvement: {speedup:.2f}x {'faster' if speedup > 1 else 'slower'}")
    print(f"  Additional line items found: {additional_items}")
    
    return {
        'receipt': receipt_path.name,
        'negative_space': ns_results,
        'vertical_alignment': va_results,
        'speedup': speedup,
        'additional_items': additional_items
    }


def create_sample_receipt_data():
    """Create sample receipt data for testing if no local data exists"""
    sample_data = {
        'words': [
            # Header
            {'id': 'w1', 'image_id': 'img1', 'text': 'WALMART', 'confidence': 0.95,
             'bounding_box': {'x': 0.4, 'y': 0.05, 'width': 0.2, 'height': 0.03}},
            {'id': 'w2', 'image_id': 'img1', 'text': 'STORE', 'confidence': 0.95,
             'bounding_box': {'x': 0.42, 'y': 0.08, 'width': 0.16, 'height': 0.02}},
            
            # Items
            {'id': 'w3', 'image_id': 'img1', 'text': 'MILK', 'confidence': 0.95,
             'bounding_box': {'x': 0.1, 'y': 0.2, 'width': 0.1, 'height': 0.02}},
            {'id': 'w4', 'image_id': 'img1', 'text': '2%', 'confidence': 0.95,
             'bounding_box': {'x': 0.21, 'y': 0.2, 'width': 0.05, 'height': 0.02}},
            {'id': 'w5', 'image_id': 'img1', 'text': '3.99', 'confidence': 0.95,
             'bounding_box': {'x': 0.8, 'y': 0.2, 'width': 0.1, 'height': 0.02}},
            
            {'id': 'w6', 'image_id': 'img1', 'text': 'BREAD', 'confidence': 0.95,
             'bounding_box': {'x': 0.1, 'y': 0.25, 'width': 0.12, 'height': 0.02}},
            {'id': 'w7', 'image_id': 'img1', 'text': '2.49', 'confidence': 0.95,
             'bounding_box': {'x': 0.8, 'y': 0.25, 'width': 0.1, 'height': 0.02}},
            
            {'id': 'w8', 'image_id': 'img1', 'text': 'EGGS', 'confidence': 0.95,
             'bounding_box': {'x': 0.1, 'y': 0.3, 'width': 0.1, 'height': 0.02}},
            {'id': 'w9', 'image_id': 'img1', 'text': 'DOZEN', 'confidence': 0.95,
             'bounding_box': {'x': 0.21, 'y': 0.3, 'width': 0.12, 'height': 0.02}},
            {'id': 'w10', 'image_id': 'img1', 'text': '4.99', 'confidence': 0.95,
             'bounding_box': {'x': 0.8, 'y': 0.3, 'width': 0.1, 'height': 0.02}},
            
            # Total
            {'id': 'w11', 'image_id': 'img1', 'text': 'TOTAL', 'confidence': 0.95,
             'bounding_box': {'x': 0.1, 'y': 0.45, 'width': 0.15, 'height': 0.03}},
            {'id': 'w12', 'image_id': 'img1', 'text': '11.47', 'confidence': 0.95,
             'bounding_box': {'x': 0.8, 'y': 0.45, 'width': 0.12, 'height': 0.03}},
        ],
        'pattern_matches': [
            {'pattern_type': 'CURRENCY', 'matched_text': '3.99', 'confidence': 0.9},
            {'pattern_type': 'CURRENCY', 'matched_text': '2.49', 'confidence': 0.9},
            {'pattern_type': 'CURRENCY', 'matched_text': '4.99', 'confidence': 0.9},
            {'pattern_type': 'CURRENCY', 'matched_text': '11.47', 'confidence': 0.9},
        ]
    }
    
    # Save sample data
    sample_path = Path('sample_receipt.json')
    with open(sample_path, 'w') as f:
        json.dump(sample_data, f, indent=2)
    
    return sample_path


def main():
    """Main function to run the integration test"""
    print("Negative Space Detection Integration Test")
    print("=" * 60)
    
    # Look for local receipt data
    receipt_data_dir = Path('receipt_data_with_labels')
    
    if not receipt_data_dir.exists():
        print("\nNo local receipt data found. Creating sample data...")
        sample_path = create_sample_receipt_data()
        receipt_files = [sample_path]
    else:
        receipt_files = list(receipt_data_dir.glob('*.json'))[:5]  # Test first 5 receipts
        print(f"\nFound {len(receipt_files)} receipt files")
    
    if not receipt_files:
        print("No receipt files to process!")
        return
    
    # Run comparison for each receipt
    all_results = []
    total_speedup = 0
    total_additional_items = 0
    
    for receipt_file in receipt_files:
        try:
            results = compare_approaches(receipt_file)
            all_results.append(results)
            total_speedup += results['speedup']
            total_additional_items += results['additional_items']
        except Exception as e:
            print(f"\nError processing {receipt_file.name}: {str(e)}")
            continue
    
    # Summary statistics
    if all_results:
        print("\n" + "=" * 60)
        print("SUMMARY STATISTICS")
        print("=" * 60)
        
        avg_speedup = total_speedup / len(all_results)
        print(f"Average speed improvement: {avg_speedup:.2f}x")
        print(f"Total additional line items detected: {total_additional_items}")
        print(f"Average additional items per receipt: {total_additional_items/len(all_results):.1f}")
        
        # Performance breakdown
        ns_times = [r['negative_space']['processing_time'] for r in all_results]
        va_times = [r['vertical_alignment']['processing_time'] for r in all_results]
        
        print(f"\nProcessing times:")
        print(f"  Negative Space: {sum(ns_times)/len(ns_times)*1000:.2f}ms average")
        print(f"  Vertical Alignment: {sum(va_times)/len(va_times)*1000:.2f}ms average")
        
        # Detection effectiveness
        total_boundaries = sum(r['negative_space']['line_item_boundaries_found'] for r in all_results)
        total_va_items = sum(r['vertical_alignment']['line_items_found'] for r in all_results)
        
        print(f"\nDetection effectiveness:")
        print(f"  Negative Space boundaries: {total_boundaries}")
        print(f"  Vertical Alignment items: {total_va_items}")
        
        # Feature analysis
        receipts_with_columns = sum(1 for r in all_results if r['negative_space']['has_column_structure'])
        print(f"\nStructural analysis:")
        print(f"  Receipts with column structure: {receipts_with_columns}/{len(all_results)}")
        
        avg_gaps = sum(r['negative_space']['whitespace_regions_found'] for r in all_results) / len(all_results)
        print(f"  Average whitespace regions per receipt: {avg_gaps:.1f}")


if __name__ == "__main__":
    main()