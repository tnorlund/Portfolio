#!/usr/bin/env python3
"""
Baseline metrics evaluation for multicolumn handler improvements.

This script evaluates the performance improvements from the MultiColumn Handler
by comparing basic spatial detection with enhanced multicolumn detection on
real production receipt data.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple, Any
from collections import defaultdict
from dataclasses import dataclass

# Add parent directory to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from receipt_label.spatial.multicolumn_handler import MultiColumnHandler, create_enhanced_line_item_detector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_dynamo.entities.receipt_word import ReceiptWord


@dataclass
class BaselineMetrics:
    """Metrics for baseline evaluation."""
    receipts_processed: int = 0
    total_processing_time: float = 0.0
    columns_detected: int = 0
    line_items_found: int = 0
    structured_data_extracted: int = 0
    needs_llm: int = 0
    validation_failures: int = 0
    error_count: int = 0


@dataclass
class EnhancedMetrics(BaselineMetrics):
    """Enhanced metrics for multicolumn handler."""
    column_types_detected: int = 0
    complete_items: int = 0
    validated_items: int = 0
    confidence_boosted: int = 0
    mathematical_validations: int = 0


def create_receipt_word_from_data(word_data: Dict[str, Any]) -> ReceiptWord:
    """Create ReceiptWord from exported JSON data."""
    try:
        # Ensure bounding box has required fields
        bounding_box = word_data.get('bounding_box', {})
        if not all(key in bounding_box for key in ['x', 'y', 'width', 'height']):
            bounding_box = {"x": 0, "y": 0, "width": 0.1, "height": 0.02}
        
        # Ensure corner points have required fields
        def ensure_point(point_data):
            if isinstance(point_data, dict) and 'x' in point_data and 'y' in point_data:
                return point_data
            return {"x": 0, "y": 0}
        
        return ReceiptWord(
            receipt_id=word_data.get('receipt_id', 1),
            image_id=word_data.get('image_id', '00000000-0000-0000-0000-000000000000'),
            line_id=word_data.get('line_id', 1),
            word_id=word_data.get('word_id', 1),
            text=word_data.get('text', ''),
            bounding_box=bounding_box,
            top_left=ensure_point(word_data.get('top_left', {})),
            top_right=ensure_point(word_data.get('top_right', {})),
            bottom_left=ensure_point(word_data.get('bottom_left', {})),
            bottom_right=ensure_point(word_data.get('bottom_right', {})),
            angle_degrees=word_data.get('angle_degrees', 0.0),
            angle_radians=word_data.get('angle_radians', 0.0),
            confidence=word_data.get('confidence', 0.9),
            extracted_data=word_data.get('extracted_data', {}),
            embedding_status=word_data.get('embedding_status', 'NONE')
        )
    except Exception as e:
        # Handle malformed data gracefully
        return None


def extract_patterns_from_receipt(receipt_data: Dict[str, Any]) -> Tuple[List[ReceiptWord], Dict[str, List[PatternMatch]]]:
    """Extract words and patterns from receipt data."""
    words = []
    patterns = defaultdict(list)
    
    # Convert receipt data to ReceiptWord objects
    for word_data in receipt_data.get('words', []):
        word = create_receipt_word_from_data(word_data)
        if word is None:
            continue
            
        words.append(word)
        
        # Check if word has a label that indicates a pattern
        label = word_data.get('label', '')
        
        if label in ['UNIT_PRICE', 'LINE_TOTAL', 'PRICE', 'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX']:
            # Extract numeric value for currency patterns
            try:
                text = word.text.replace('$', '').replace(',', '').strip()
                extracted_value = float(text) if text.replace('.', '').replace('-', '').isdigit() else 0.0
            except (ValueError, AttributeError):
                extracted_value = 0.0
            
            pattern = PatternMatch(
                word=word,
                pattern_type=PatternType.CURRENCY,
                confidence=0.9,
                matched_text=word.text,
                extracted_value=extracted_value,
                metadata={'original_label': label}
            )
            patterns['currency'].append(pattern)
        
        elif label == 'QUANTITY':
            # Extract numeric value for quantity patterns
            try:
                import re
                match = re.search(r'(\d+(?:\.\d+)?)', word.text)
                extracted_value = float(match.group(1)) if match else 1.0
            except (ValueError, AttributeError):
                extracted_value = 1.0
                
            pattern = PatternMatch(
                word=word,
                pattern_type=PatternType.QUANTITY,
                confidence=0.8,
                matched_text=word.text,
                extracted_value=extracted_value,
                metadata={'original_label': label}
            )
            patterns['quantity'].append(pattern)
    
    return words, patterns


def evaluate_basic_detector(words: List[ReceiptWord], patterns: Dict[str, List[PatternMatch]]) -> Dict[str, Any]:
    """Evaluate receipt using basic vertical alignment detector."""
    start_time = time.time()
    
    try:
        detector = VerticalAlignmentDetector(use_enhanced_clustering=True)
        
        currency_patterns = patterns.get('currency', [])
        if not currency_patterns:
            return {
                'processing_time': time.time() - start_time,
                'columns_detected': 0,
                'line_items_found': 0,
                'structured_data_extracted': False,
                'needs_llm': True,
                'error': False
            }
        
        # Detect price columns
        columns = detector.detect_price_columns(currency_patterns)
        
        # Try to find line items
        try:
            line_items_result = detector.detect_line_items_with_alignment(words, currency_patterns)
            line_items_count = 0
            
            if isinstance(line_items_result, dict):
                line_items_count = len(line_items_result.get('line_items', []))
            elif isinstance(line_items_result, list):
                line_items_count = len(line_items_result)
        except Exception:
            line_items_count = 0
        
        return {
            'processing_time': time.time() - start_time,
            'columns_detected': len(columns),
            'line_items_found': line_items_count,
            'structured_data_extracted': len(columns) > 0,
            'needs_llm': len(columns) == 0 or line_items_count == 0,
            'error': False
        }
    
    except Exception as e:
        return {
            'processing_time': time.time() - start_time,
            'columns_detected': 0,
            'line_items_found': 0,
            'structured_data_extracted': False,
            'needs_llm': True,
            'error': True,
            'error_message': str(e)
        }


def evaluate_multicolumn_handler(words: List[ReceiptWord], patterns: Dict[str, List[PatternMatch]]) -> Dict[str, Any]:
    """Evaluate receipt using multicolumn handler."""
    start_time = time.time()
    
    try:
        handler = create_enhanced_line_item_detector()
        
        currency_patterns = patterns.get('currency', [])
        quantity_patterns = patterns.get('quantity', [])
        
        # Detect column structure
        columns = handler.detect_column_structure(words, currency_patterns, quantity_patterns)
        
        # Assemble line items
        line_items = handler.assemble_line_items(columns, words, patterns)
        
        # Analyze results
        complete_items = sum(1 for item in line_items if item.description and item.line_total is not None)
        validated_items = sum(1 for item in line_items if item.validation_status.get('quantity_price_total', False))
        confidence_boosted = sum(1 for item in line_items if item.confidence > 0.8)
        mathematical_validations = sum(len(item.validation_status) for item in line_items)
        
        has_complete_items = complete_items > 0
        has_validated_items = validated_items > 0
        
        return {
            'processing_time': time.time() - start_time,
            'columns_detected': len(columns),
            'column_types': [col.column_type.value for col in columns.values()],
            'line_items_found': len(line_items),
            'complete_items': complete_items,
            'validated_items': validated_items,
            'confidence_boosted': confidence_boosted,
            'mathematical_validations': mathematical_validations,
            'structured_data_extracted': has_complete_items,
            'needs_llm': not has_complete_items,
            'confidence_boost': has_validated_items,
            'error': False
        }
    
    except Exception as e:
        return {
            'processing_time': time.time() - start_time,
            'columns_detected': 0,
            'line_items_found': 0,
            'complete_items': 0,
            'validated_items': 0,
            'structured_data_extracted': False,
            'needs_llm': True,
            'error': True,
            'error_message': str(e)
        }


def load_receipt_files(data_dir: str = "./receipt_data_with_labels", limit: int = 100) -> List[Dict[str, Any]]:
    """Load receipt files from exported data."""
    receipts = []
    data_path = Path(data_dir)
    
    if not data_path.exists():
        print(f"❌ Data directory not found: {data_dir}")
        return []
    
    json_files = list(data_path.glob("*.json"))[:limit]
    print(f"📂 Found {len(json_files)} receipt files, processing {min(limit, len(json_files))}")
    
    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
                receipts.append(data)
        except Exception as e:
            print(f"❌ Error loading {json_file}: {e}")
    
    return receipts


def print_metrics_comparison(baseline: BaselineMetrics, enhanced: EnhancedMetrics):
    """Print comparison of baseline vs enhanced metrics."""
    print("\n📊 Performance Comparison")
    print("=" * 80)
    
    # Processing metrics
    print(f"\n🔍 Processing Metrics:")
    print(f"  Receipts processed:     {baseline.receipts_processed}")
    
    if baseline.receipts_processed > 0 and enhanced.receipts_processed > 0:
        print(f"  Avg processing time:    {baseline.total_processing_time/baseline.receipts_processed*1000:.1f}ms (basic) vs {enhanced.total_processing_time/enhanced.receipts_processed*1000:.1f}ms (enhanced)")
        print(f"  Error rate:             {baseline.error_count/baseline.receipts_processed*100:.1f}% (basic) vs {enhanced.error_count/enhanced.receipts_processed*100:.1f}% (enhanced)")
    else:
        print("  Avg processing time:    N/A (no receipts processed)")
        print("  Error rate:             N/A (no receipts processed)")
    
    # Column detection
    print(f"\n📋 Column Detection:")
    if baseline.receipts_processed > 0 and enhanced.receipts_processed > 0:
        print(f"  Avg columns detected:   {baseline.columns_detected/baseline.receipts_processed:.1f} (basic) vs {enhanced.columns_detected/enhanced.receipts_processed:.1f} (enhanced)")
    else:
        print(f"  Avg columns detected:   N/A (no receipts processed)")
    print(f"  Column types detected:  N/A (basic) vs {enhanced.column_types_detected} unique types (enhanced)")
    
    # Line item extraction
    print(f"\n📝 Line Item Extraction:")
    print(f"  Total line items:       {baseline.line_items_found} (basic) vs {enhanced.line_items_found} (enhanced)")
    print(f"  Complete items:         N/A (basic) vs {enhanced.complete_items} (enhanced)")
    print(f"  Validated items:        N/A (basic) vs {enhanced.validated_items} (enhanced)")
    
    # LLM dependency
    print(f"\n🤖 LLM Dependency:")
    if baseline.receipts_processed > 0:
        print(f"  Needs LLM:              {baseline.needs_llm}/{baseline.receipts_processed} ({baseline.needs_llm/baseline.receipts_processed*100:.1f}%) (basic)")
    else:
        print(f"  Needs LLM:              N/A (no receipts processed) (basic)")
    
    if enhanced.receipts_processed > 0:
        print(f"                          {enhanced.needs_llm}/{enhanced.receipts_processed} ({enhanced.needs_llm/enhanced.receipts_processed*100:.1f}%) (enhanced)")
    else:
        print(f"                          N/A (no receipts processed) (enhanced)")
    
    if baseline.needs_llm > enhanced.needs_llm and baseline.needs_llm > 0:
        reduction = (baseline.needs_llm - enhanced.needs_llm) / baseline.needs_llm * 100
        print(f"  🎯 LLM reduction:       {reduction:.1f}%")
    
    # Enhanced-specific metrics
    print(f"\n✨ Enhanced Features:")
    print(f"  Mathematical validations: {enhanced.mathematical_validations}")
    print(f"  Confidence boosted:       {enhanced.confidence_boosted} items")
    print(f"  Structured data rate:     {enhanced.structured_data_extracted}/{enhanced.receipts_processed} ({enhanced.structured_data_extracted/enhanced.receipts_processed*100:.1f}%)")


def main():
    """Run baseline metrics evaluation."""
    print("📊 MultiColumn Handler Baseline Metrics Evaluation")
    print("=" * 80)
    
    # Load receipt data
    receipts = load_receipt_files(limit=50)  # Start with 50 receipts for performance
    
    if not receipts:
        print("❌ No receipt data found. Please run export_all_receipts.py first.")
        return
    
    print(f"🔄 Processing {len(receipts)} receipts...")
    
    # Initialize metrics
    baseline_metrics = BaselineMetrics()
    enhanced_metrics = EnhancedMetrics()
    
    # Track column types
    all_column_types = set()
    
    # Process each receipt
    for i, receipt_data in enumerate(receipts):
        print(f"\rProcessing receipt {i+1}/{len(receipts)}...", end='', flush=True)
        
        try:
            # Extract words and patterns
            words, patterns = extract_patterns_from_receipt(receipt_data)
            
            # Skip if no patterns found
            if not any(patterns.values()):
                continue
            
            # Evaluate with basic detector
            basic_result = evaluate_basic_detector(words, patterns)
            baseline_metrics.receipts_processed += 1
            baseline_metrics.total_processing_time += basic_result['processing_time']
            baseline_metrics.columns_detected += basic_result['columns_detected']
            baseline_metrics.line_items_found += basic_result['line_items_found']
            baseline_metrics.structured_data_extracted += 1 if basic_result['structured_data_extracted'] else 0
            baseline_metrics.needs_llm += 1 if basic_result['needs_llm'] else 0
            baseline_metrics.error_count += 1 if basic_result['error'] else 0
            
            # Evaluate with multicolumn handler
            enhanced_result = evaluate_multicolumn_handler(words, patterns)
            enhanced_metrics.receipts_processed += 1
            enhanced_metrics.total_processing_time += enhanced_result['processing_time']
            enhanced_metrics.columns_detected += enhanced_result['columns_detected']
            enhanced_metrics.line_items_found += enhanced_result['line_items_found']
            enhanced_metrics.complete_items += enhanced_result.get('complete_items', 0)
            enhanced_metrics.validated_items += enhanced_result.get('validated_items', 0)
            enhanced_metrics.confidence_boosted += enhanced_result.get('confidence_boosted', 0)
            enhanced_metrics.mathematical_validations += enhanced_result.get('mathematical_validations', 0)
            enhanced_metrics.structured_data_extracted += 1 if enhanced_result['structured_data_extracted'] else 0
            enhanced_metrics.needs_llm += 1 if enhanced_result['needs_llm'] else 0
            enhanced_metrics.error_count += 1 if enhanced_result['error'] else 0
            
            # Track column types
            column_types = enhanced_result.get('column_types', [])
            all_column_types.update(column_types)
            
        except Exception as e:
            print(f"\n❌ Error processing receipt {i+1}: {e}")
            baseline_metrics.error_count += 1
            enhanced_metrics.error_count += 1
    
    enhanced_metrics.column_types_detected = len(all_column_types)
    
    print(f"\n\n✅ Completed processing {baseline_metrics.receipts_processed} receipts")
    
    # Print results
    print_metrics_comparison(baseline_metrics, enhanced_metrics)
    
    # Summary insights
    print(f"\n💡 Key Insights:")
    
    if enhanced_metrics.columns_detected > baseline_metrics.columns_detected and baseline_metrics.columns_detected > 0:
        improvement = (enhanced_metrics.columns_detected - baseline_metrics.columns_detected) / baseline_metrics.columns_detected * 100
        print(f"  • {improvement:.1f}% more columns detected with enhanced handler")
    
    if enhanced_metrics.validated_items > 0 and enhanced_metrics.line_items_found > 0:
        validation_rate = enhanced_metrics.validated_items / enhanced_metrics.line_items_found * 100
        print(f"  • {validation_rate:.1f}% of line items mathematically validated")
    
    if baseline_metrics.needs_llm > enhanced_metrics.needs_llm and baseline_metrics.receipts_processed > 0:
        cost_reduction = (baseline_metrics.needs_llm - enhanced_metrics.needs_llm) / baseline_metrics.receipts_processed * 100
        print(f"  • {cost_reduction:.1f}% reduction in LLM dependency per receipt")
    
    print(f"  • {len(all_column_types)} different column types detected: {', '.join(sorted(all_column_types))}")
    
    print(f"\n🎯 Conclusion:")
    if enhanced_metrics.structured_data_extracted > baseline_metrics.structured_data_extracted:
        print("  The MultiColumn Handler significantly improves structured data extraction")
        print("  and reduces reliance on expensive LLM calls for line item detection.")
    else:
        print("  The MultiColumn Handler provides similar performance with enhanced features")
        print("  like mathematical validation and detailed column classification.")


if __name__ == "__main__":
    main()