#!/usr/bin/env python3
"""
Baseline metrics for line item detection enhancement evaluation.

This script evaluates the performance of different line item detection approaches:
1. Pattern-only detection (existing baseline)
2. Spatial detection (Phase 2)
3. Negative space detection (this enhancement)
4. Combined approach

Metrics measured:
- Line items detected per receipt
- Processing time
- Cost reduction estimates
- Accuracy indicators
"""

import json
import time
import logging
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import detection components
from receipt_label.models.receipt import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.negative_space_detector import NegativeSpaceDetector

# Configure logging
logging.basicConfig(level=logging.WARNING)  # Suppress info logs during evaluation


@dataclass
class DetectionMetrics:
    """Metrics for a single detection approach"""
    approach_name: str
    processing_time_ms: float
    line_items_detected: int
    boundaries_found: int
    has_column_structure: bool
    confidence_scores: List[float]
    whitespace_regions: int = 0
    new_items_vs_baseline: int = 0
    

@dataclass
class ReceiptEvaluation:
    """Complete evaluation results for a single receipt"""
    receipt_id: str
    word_count: int
    pattern_matches_count: int
    baseline_metrics: DetectionMetrics
    spatial_metrics: DetectionMetrics
    negative_space_metrics: DetectionMetrics
    combined_metrics: DetectionMetrics
    

def load_receipt_data(file_path: Path) -> Tuple[List[ReceiptWord], List[PatternMatch], str]:
    """Load receipt data from exported JSON file"""
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    receipt_id = file_path.stem
    
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
            image_id=word_data.get('image_id', receipt_id)
        )
        words.append(word)
    
    # Create pattern matches from existing labels or currency patterns
    pattern_matches = []
    
    # Try to load existing pattern matches
    for match_data in data.get('pattern_matches', []):
        matched_text = match_data.get('matched_text', '')
        matching_word = next((w for w in words if w.text == matched_text), None)
        
        if matching_word:
            pattern_type = PatternType.CURRENCY if match_data.get('pattern_type', '') == 'CURRENCY' else PatternType.PRODUCT_NAME
            
            # Handle currency parsing safely
            if pattern_type == PatternType.CURRENCY:
                try:
                    # Remove currency symbols and commas before converting to float
                    cleaned_text = matched_text.replace('$', '').replace(',', '').strip()
                    extracted_value = float(cleaned_text)
                except ValueError:
                    # Skip this match if we can't parse the currency
                    continue
            else:
                extracted_value = matched_text
            
            match = PatternMatch(
                word=matching_word,
                pattern_type=pattern_type,
                matched_text=matched_text,
                confidence=match_data.get('confidence', 0.0),
                extracted_value=extracted_value,
                metadata=match_data.get('metadata', {})
            )
            pattern_matches.append(match)
    
    # If no pattern matches, create basic currency matches
    if not pattern_matches:
        import re
        currency_pattern = re.compile(r'\$?(\d+\.\d{2})|\$?(\d+,\d{3}(?:\.\d{2})?)')
        
        for word in words:
            if currency_pattern.match(word.text):
                try:
                    value = float(word.text.replace('$', '').replace(',', ''))
                    if 0.01 <= value <= 999.99:  # Reasonable price range
                        match = PatternMatch(
                            word=word,
                            pattern_type=PatternType.CURRENCY,
                            matched_text=word.text,
                            confidence=0.8,
                            extracted_value=value,
                            metadata={'source': 'baseline_currency_detection'}
                        )
                        pattern_matches.append(match)
                except ValueError:
                    continue
    
    return words, pattern_matches, receipt_id


def evaluate_baseline_detection(words: List[ReceiptWord], pattern_matches: List[PatternMatch]) -> DetectionMetrics:
    """Evaluate baseline pattern-only detection"""
    start_time = time.time()
    
    # Baseline just counts currency patterns as line items
    currency_matches = [m for m in pattern_matches if m.pattern_type == PatternType.CURRENCY]
    line_items = len(currency_matches)
    
    # Extract confidence scores
    confidence_scores = [m.confidence for m in currency_matches]
    
    processing_time = (time.time() - start_time) * 1000
    
    return DetectionMetrics(
        approach_name="Baseline (Pattern-only)",
        processing_time_ms=processing_time,
        line_items_detected=line_items,
        boundaries_found=line_items,
        has_column_structure=False,
        confidence_scores=confidence_scores
    )


def evaluate_spatial_detection(words: List[ReceiptWord], pattern_matches: List[PatternMatch]) -> DetectionMetrics:
    """Evaluate Phase 2 spatial detection"""
    start_time = time.time()
    
    try:
        # Use vertical alignment detector (Phase 2)
        detector = VerticalAlignmentDetector(use_enhanced_clustering=True)
        result = detector.detect_line_items_with_alignment(words, pattern_matches)
        
        line_items = len(result.get('line_items', []))
        confidence_scores = [item.get('confidence', 0.0) for item in result.get('line_items', [])]
        has_columns = len(result.get('price_columns', [])) > 0
        
    except Exception as e:
        print(f"Warning: Spatial detection failed: {e}")
        line_items = 0
        confidence_scores = []
        has_columns = False
    
    processing_time = (time.time() - start_time) * 1000
    
    return DetectionMetrics(
        approach_name="Spatial (Phase 2)",
        processing_time_ms=processing_time,
        line_items_detected=line_items,
        boundaries_found=line_items,
        has_column_structure=has_columns,
        confidence_scores=confidence_scores
    )


def evaluate_negative_space_detection(words: List[ReceiptWord], pattern_matches: List[PatternMatch], baseline_count: int) -> DetectionMetrics:
    """Evaluate negative space detection"""
    start_time = time.time()
    
    detector = NegativeSpaceDetector()
    
    # Detect whitespace regions
    whitespace_regions = detector.detect_whitespace_regions(words)
    
    # Detect line item boundaries
    boundaries = detector.detect_line_item_boundaries(words, whitespace_regions, pattern_matches)
    
    # Detect column structure
    column_structure = detector.detect_column_structure(words)
    
    # Count items with prices (actual line items)
    item_boundaries = [b for b in boundaries if b.has_price]
    line_items = len(item_boundaries)
    
    # Extract confidence scores
    confidence_scores = [b.confidence for b in item_boundaries]
    
    processing_time = (time.time() - start_time) * 1000
    
    return DetectionMetrics(
        approach_name="Negative Space",
        processing_time_ms=processing_time,
        line_items_detected=line_items,
        boundaries_found=len(boundaries),
        has_column_structure=column_structure is not None,
        confidence_scores=confidence_scores,
        whitespace_regions=len(whitespace_regions),
        new_items_vs_baseline=max(0, line_items - baseline_count)
    )


def evaluate_combined_detection(words: List[ReceiptWord], pattern_matches: List[PatternMatch], baseline_count: int) -> DetectionMetrics:
    """Evaluate combined spatial + negative space detection"""
    start_time = time.time()
    
    # Use negative space to enhance existing matches
    ns_detector = NegativeSpaceDetector()
    enhanced_matches = ns_detector.enhance_line_items_with_negative_space(words, pattern_matches)
    
    # Count enhanced line items
    baseline_items = len([m for m in pattern_matches if m.pattern_type == PatternType.CURRENCY])
    enhanced_items = len([m for m in enhanced_matches 
                         if m.pattern_type == PatternType.PRODUCT_NAME 
                         and m.metadata.get("detection_method") == "negative_space"])
    
    total_items = baseline_items + enhanced_items
    
    # Detect column structure for additional context
    column_structure = ns_detector.detect_column_structure(words)
    
    # Extract confidence scores from enhanced matches
    confidence_scores = [m.confidence for m in enhanced_matches 
                        if m.metadata.get("detection_method") == "negative_space"]
    
    processing_time = (time.time() - start_time) * 1000
    
    return DetectionMetrics(
        approach_name="Combined (Spatial + Negative Space)",
        processing_time_ms=processing_time,
        line_items_detected=total_items,
        boundaries_found=total_items,
        has_column_structure=column_structure is not None,
        confidence_scores=confidence_scores,
        new_items_vs_baseline=enhanced_items
    )


def evaluate_receipt(file_path: Path) -> Optional[ReceiptEvaluation]:
    """Evaluate all detection approaches on a single receipt"""
    try:
        # Load receipt data
        words, pattern_matches, receipt_id = load_receipt_data(file_path)
        
        if not words:
            print(f"Skipping {receipt_id}: No words found")
            return None
        
        print(f"Evaluating {receipt_id}: {len(words)} words, {len(pattern_matches)} patterns")
        
        # Run all evaluations
        baseline_metrics = evaluate_baseline_detection(words, pattern_matches)
        spatial_metrics = evaluate_spatial_detection(words, pattern_matches)
        negative_space_metrics = evaluate_negative_space_detection(words, pattern_matches, baseline_metrics.line_items_detected)
        combined_metrics = evaluate_combined_detection(words, pattern_matches, baseline_metrics.line_items_detected)
        
        return ReceiptEvaluation(
            receipt_id=receipt_id,
            word_count=len(words),
            pattern_matches_count=len(pattern_matches),
            baseline_metrics=baseline_metrics,
            spatial_metrics=spatial_metrics,
            negative_space_metrics=negative_space_metrics,
            combined_metrics=combined_metrics
        )
        
    except Exception as e:
        print(f"Error evaluating {file_path.name}: {e}")
        return None


def generate_summary_report(evaluations: List[ReceiptEvaluation]) -> Dict:
    """Generate summary statistics across all evaluations"""
    if not evaluations:
        return {}
    
    # Aggregate metrics by approach
    approaches = ['baseline_metrics', 'spatial_metrics', 'negative_space_metrics', 'combined_metrics']
    summary = {}
    
    for approach in approaches:
        metrics_list = [getattr(eval_result, approach) for eval_result in evaluations]
        approach_name = metrics_list[0].approach_name
        
        summary[approach_name] = {
            'total_receipts': len(evaluations),
            'avg_processing_time_ms': sum(m.processing_time_ms for m in metrics_list) / len(metrics_list),
            'total_line_items_detected': sum(m.line_items_detected for m in metrics_list),
            'avg_line_items_per_receipt': sum(m.line_items_detected for m in metrics_list) / len(metrics_list),
            'receipts_with_column_structure': sum(1 for m in metrics_list if m.has_column_structure),
            'avg_confidence': sum(sum(m.confidence_scores) for m in metrics_list) / max(1, sum(len(m.confidence_scores) for m in metrics_list)),
            'total_whitespace_regions': sum(getattr(m, 'whitespace_regions', 0) for m in metrics_list),
            'total_new_items_vs_baseline': sum(getattr(m, 'new_items_vs_baseline', 0) for m in metrics_list)
        }
    
    # Calculate improvement metrics
    baseline_items = summary[evaluations[0].baseline_metrics.approach_name]['total_line_items_detected']
    
    for approach_name, stats in summary.items():
        if approach_name != evaluations[0].baseline_metrics.approach_name:
            improvement = stats['total_line_items_detected'] - baseline_items
            stats['improvement_vs_baseline'] = improvement
            stats['improvement_percentage'] = (improvement / max(1, baseline_items)) * 100
    
    return summary


def main():
    """Main evaluation function"""
    print("Line Item Detection Enhancement Evaluation")
    print("=" * 60)
    
    # Find receipt data directory
    data_dir = Path("receipt_data_with_labels")
    if not data_dir.exists():
        # Try alternative locations
        for alt_dir in ["./receipt_data", "../receipt_data_with_labels", "../../receipt_data_with_labels", "../../../receipt_data_with_labels"]:
            alt_path = Path(alt_dir)
            if alt_path.exists():
                data_dir = alt_path
                break
        else:
            print(f"❌ No receipt data found. Please run export_all_receipts.py first")
            print(f"   Looking for: {data_dir.absolute()}")
            print(f"   Current directory: {Path.cwd()}")
            return
    
    # Find JSON files
    json_files = list(data_dir.glob("*.json"))
    if not json_files:
        print(f"❌ No JSON files found in {data_dir}")
        return
    
    print(f"📁 Found {len(json_files)} receipt files in {data_dir}")
    
    # Limit to first N receipts for testing
    max_receipts = 20
    if len(json_files) > max_receipts:
        json_files = json_files[:max_receipts]
        print(f"📊 Evaluating first {max_receipts} receipts for testing")
    
    # Evaluate all receipts
    evaluations = []
    failed_count = 0
    
    for i, file_path in enumerate(json_files, 1):
        print(f"\n[{i}/{len(json_files)}] Processing {file_path.name}...")
        
        evaluation = evaluate_receipt(file_path)
        if evaluation:
            evaluations.append(evaluation)
            
            # Show quick results
            ns_metrics = evaluation.negative_space_metrics
            print(f"  ✅ Negative Space: {ns_metrics.line_items_detected} items, "
                  f"{ns_metrics.processing_time_ms:.1f}ms, "
                  f"{ns_metrics.whitespace_regions} regions")
        else:
            failed_count += 1
    
    if not evaluations:
        print("❌ No successful evaluations")
        return
    
    # Generate summary report
    print(f"\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    
    summary = generate_summary_report(evaluations)
    
    for approach_name, stats in summary.items():
        print(f"\n📊 {approach_name}:")
        print(f"   Receipts processed: {stats['total_receipts']}")
        print(f"   Avg processing time: {stats['avg_processing_time_ms']:.2f}ms")
        print(f"   Total line items: {stats['total_line_items_detected']}")
        print(f"   Avg items per receipt: {stats['avg_line_items_per_receipt']:.1f}")
        print(f"   Receipts with columns: {stats['receipts_with_column_structure']}")
        print(f"   Avg confidence: {stats['avg_confidence']:.2f}")
        
        if 'improvement_vs_baseline' in stats:
            improvement = stats['improvement_vs_baseline']
            percentage = stats['improvement_percentage']
            print(f"   📈 Improvement: +{improvement} items ({percentage:+.1f}%)")
        
        if stats.get('total_whitespace_regions', 0) > 0:
            print(f"   🔍 Whitespace regions: {stats['total_whitespace_regions']}")
        
        if stats.get('total_new_items_vs_baseline', 0) > 0:
            print(f"   🆕 New items detected: {stats['total_new_items_vs_baseline']}")
    
    # Performance comparison
    print(f"\n📈 PERFORMANCE COMPARISON:")
    baseline_time = summary[evaluations[0].baseline_metrics.approach_name]['avg_processing_time_ms']
    
    for approach_name, stats in summary.items():
        if approach_name != evaluations[0].baseline_metrics.approach_name:
            time_ratio = stats['avg_processing_time_ms'] / baseline_time
            print(f"   {approach_name}: {time_ratio:.2f}x baseline processing time")
    
    # Save detailed results
    results_file = Path("evaluation_results.json")
    detailed_results = {
        'summary': summary,
        'detailed_evaluations': [asdict(eval_result) for eval_result in evaluations],
        'metadata': {
            'total_receipts_processed': len(evaluations),
            'failed_evaluations': failed_count,
            'data_directory': str(data_dir),
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
    }
    
    with open(results_file, 'w') as f:
        json.dump(detailed_results, f, indent=2)
    
    print(f"\n💾 Detailed results saved to: {results_file}")
    print(f"📊 Total receipts: {len(evaluations)} successful, {failed_count} failed")


if __name__ == "__main__":
    main()