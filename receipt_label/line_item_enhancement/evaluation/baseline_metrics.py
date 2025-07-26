#!/usr/bin/env python3
"""
Baseline metrics evaluation for horizontal grouping line item detection.

This script evaluates the horizontal grouping implementation against production
receipt data to measure the effectiveness of reducing LLM dependency.
"""

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import time

# Set up logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add project root to path  
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
receipt_label_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(receipt_label_root))

try:
    from receipt_dynamo.entities.receipt_word import ReceiptWord
    from receipt_label.pattern_detection.base import PatternMatch, PatternType
    from receipt_label.spatial.geometry_utils import (
        is_horizontally_aligned_group,
        group_words_into_line_items,
        LineItemSpatialDetector
    )
    from receipt_label.spatial.horizontal_line_item_detector import (
        HorizontalLineItemDetector,
        HorizontalGroupingConfig,
        LineItem
    )
except ImportError as e:
    logger.error(f"Import error: {e}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path[:3]}")
    raise


@dataclass
class ReceiptEvaluationResult:
    """Results for evaluating a single receipt."""
    receipt_id: str
    total_words: int
    line_items_detected: int
    horizontal_groups_found: int
    processing_time_ms: float
    confidence_scores: List[float]
    detection_methods: List[str]
    error: Optional[str] = None


@dataclass
class BaselineMetrics:
    """Overall baseline metrics for the evaluation."""
    total_receipts: int
    successful_evaluations: int
    total_words_processed: int
    total_line_items_detected: int
    average_line_items_per_receipt: float
    average_processing_time_ms: float
    average_confidence: float
    horizontal_grouping_success_rate: float
    error_rate: float
    errors: List[str]


def load_receipt_data(file_path: Path) -> Optional[Dict]:
    """Load receipt data from JSON file."""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return None


def convert_to_receipt_words(words_data: List[Dict]) -> List[ReceiptWord]:
    """Convert word data from JSON to ReceiptWord objects."""
    receipt_words = []
    
    for i, word_data in enumerate(words_data):
        try:
            # Handle different data formats
            if 'bounding_box' in word_data:
                bbox = word_data['bounding_box']
            elif 'geometry' in word_data:
                # Convert geometry format to bounding_box
                geom = word_data['geometry']
                bbox = {
                    'x': geom.get('left', 0.0),
                    'y': geom.get('top', 0.0),
                    'width': geom.get('width', 0.1),
                    'height': geom.get('height', 0.02)
                }
            else:
                # Default bounding box if missing
                bbox = {'x': 0.0, 'y': 0.0, 'width': 0.1, 'height': 0.02}
            
            # Create ReceiptWord with proper constructor parameters
            word = ReceiptWord(
                receipt_id=1,  # Dummy receipt ID
                image_id=word_data.get('image_id', 'dummy-image-id'),
                line_id=i // 10,  # Group words into lines
                word_id=i + 1,
                text=word_data.get('text', ''),
                bounding_box=bbox,
                top_right={'x': bbox['x'] + bbox['width'], 'y': bbox['y']},
                top_left={'x': bbox['x'], 'y': bbox['y']},
                bottom_right={'x': bbox['x'] + bbox['width'], 'y': bbox['y'] + bbox['height']},
                bottom_left={'x': bbox['x'], 'y': bbox['y'] + bbox['height']},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=word_data.get('confidence', 1.0)
            )
            receipt_words.append(word)
            
        except Exception as e:
            logger.warning(f"Error converting word {i}: {e}")
            continue
    
    return receipt_words


def extract_pattern_matches(receipt_data: Dict, receipt_words: List[ReceiptWord]) -> List[PatternMatch]:
    """Extract pattern matches from receipt data and associate with ReceiptWord objects.
    
    Args:
        receipt_data: Receipt data containing labels
        receipt_words: List of ReceiptWord objects to match against
        
    Returns:
        List of PatternMatch objects with proper word associations
    """
    pattern_matches = []
    
    # Create a mapping of text to list of ReceiptWords to handle duplicates
    from collections import defaultdict
    text_to_words = defaultdict(list)
    for word in receipt_words:
        # Store by text for exact matches
        text_to_words[word.text].append(word)
        # Also store lowercase version for case-insensitive matching
        if word.text.lower() != word.text:
            text_to_words[word.text.lower()].append(word)
    
    # Look for labels or patterns in the data
    if 'labels' in receipt_data:
        labels = receipt_data['labels']
        for label in labels:
            try:
                # Convert label to PatternMatch
                pattern_type = PatternType.CURRENCY  # Default
                if 'currency' in label.get('type', '').lower():
                    pattern_type = PatternType.CURRENCY
                elif 'quantity' in label.get('type', '').lower():
                    pattern_type = PatternType.QUANTITY
                elif 'date' in label.get('type', '').lower():
                    pattern_type = PatternType.DATE
                
                # Find the corresponding ReceiptWord
                label_text = label.get('text', '')
                candidate_words = text_to_words.get(label_text, []) or text_to_words.get(label_text.lower(), [])
                
                if candidate_words:
                    # If multiple words have the same text, try to match by position if available
                    matched_word = None
                    
                    # Check if label has position information to help match
                    if len(candidate_words) == 1:
                        matched_word = candidate_words[0]
                    else:
                        # Try to match by word_id if available in label
                        if 'word_id' in label:
                            for word in candidate_words:
                                if word.word_id == label['word_id']:
                                    matched_word = word
                                    break
                        
                        # Try to match by position if available
                        if not matched_word and 'bounding_box' in label:
                            label_bbox = label['bounding_box']
                            # Find word with closest position
                            min_distance = float('inf')
                            for word in candidate_words:
                                # Calculate distance between centers
                                word_x = word.bounding_box['x'] + word.bounding_box['width'] / 2
                                word_y = word.bounding_box['y'] + word.bounding_box['height'] / 2
                                label_x = label_bbox.get('x', 0) + label_bbox.get('width', 0) / 2
                                label_y = label_bbox.get('y', 0) + label_bbox.get('height', 0) / 2
                                
                                distance = ((word_x - label_x) ** 2 + (word_y - label_y) ** 2) ** 0.5
                                if distance < min_distance:
                                    min_distance = distance
                                    matched_word = word
                        
                        # If still no match, use the first occurrence
                        if not matched_word:
                            matched_word = candidate_words[0]
                            logger.debug(f"Multiple words with text '{label_text}', using first occurrence")
                    
                    # Create PatternMatch with proper word reference
                    match = PatternMatch(
                        word=matched_word,
                        pattern_type=pattern_type,
                        confidence=label.get('confidence', 0.8),
                        matched_text=label_text,
                        extracted_value=label.get('value', ''),
                        metadata={'label_type': label.get('type', '')}
                    )
                    pattern_matches.append(match)
                else:
                    logger.debug(f"Could not find ReceiptWord for label text: {label_text}")
                
            except Exception as e:
                logger.warning(f"Error converting label to pattern: {e}")
                continue
    
    return pattern_matches


async def evaluate_single_receipt(receipt_data: Dict, receipt_id: str) -> ReceiptEvaluationResult:
    """Evaluate horizontal grouping on a single receipt."""
    
    start_time = time.time()
    
    try:
        # Extract words from receipt data
        words_data = receipt_data.get('words', [])
        if not words_data:
            return ReceiptEvaluationResult(
                receipt_id=receipt_id,
                total_words=0,
                line_items_detected=0,
                horizontal_groups_found=0,
                processing_time_ms=0,
                confidence_scores=[],
                detection_methods=[],
                error="No words found in receipt data"
            )
        
        # Convert to ReceiptWord objects
        receipt_words = convert_to_receipt_words(words_data)
        if not receipt_words:
            return ReceiptEvaluationResult(
                receipt_id=receipt_id,
                total_words=len(words_data),
                line_items_detected=0,
                horizontal_groups_found=0,
                processing_time_ms=0,
                confidence_scores=[],
                detection_methods=[],
                error="Failed to convert words to ReceiptWord objects"
            )
        
        # Extract pattern matches if available
        pattern_matches = extract_pattern_matches(receipt_data, receipt_words)
        
        # Test horizontal grouping functionality
        horizontal_groups = group_words_into_line_items(receipt_words)
        
        # Use HorizontalLineItemDetector for full analysis
        detector = HorizontalLineItemDetector(
            config=HorizontalGroupingConfig(
                min_confidence=0.3,
                gap_multiplier=3.0,
                alignment_threshold=0.7
            )
        )
        
        line_items = detector.detect_line_items(receipt_words, pattern_matches)
        
        # Calculate metrics
        processing_time_ms = (time.time() - start_time) * 1000
        confidence_scores = [item.confidence for item in line_items]
        detection_methods = [item.detection_method for item in line_items]
        
        return ReceiptEvaluationResult(
            receipt_id=receipt_id,
            total_words=len(receipt_words),
            line_items_detected=len(line_items),
            horizontal_groups_found=len(horizontal_groups),
            processing_time_ms=processing_time_ms,
            confidence_scores=confidence_scores,
            detection_methods=detection_methods
        )
        
    except Exception as e:
        processing_time_ms = (time.time() - start_time) * 1000
        logger.error(f"Error evaluating receipt {receipt_id}: {e}")
        return ReceiptEvaluationResult(
            receipt_id=receipt_id,
            total_words=0,
            line_items_detected=0,
            horizontal_groups_found=0,
            processing_time_ms=processing_time_ms,
            confidence_scores=[],
            detection_methods=[],
            error=str(e)
        )


async def evaluate_baseline_metrics(data_dir: Path, max_receipts: int = 20) -> BaselineMetrics:
    """Evaluate baseline metrics across receipt dataset."""
    
    logger.info(f"Starting baseline evaluation on {data_dir}")
    logger.info(f"Maximum receipts to process: {max_receipts}")
    
    # Find all receipt JSON files
    receipt_files = list(data_dir.glob("*.json"))[:max_receipts]
    logger.info(f"Found {len(receipt_files)} receipt files")
    
    if not receipt_files:
        logger.error("No receipt files found!")
        return BaselineMetrics(
            total_receipts=0,
            successful_evaluations=0,
            total_words_processed=0,
            total_line_items_detected=0,
            average_line_items_per_receipt=0.0,
            average_processing_time_ms=0.0,
            average_confidence=0.0,
            horizontal_grouping_success_rate=0.0,
            error_rate=1.0,
            errors=["No receipt files found"]
        )
    
    # Process receipts
    results = []
    errors = []
    
    for i, receipt_file in enumerate(receipt_files):
        logger.info(f"Processing receipt {i+1}/{len(receipt_files)}: {receipt_file.stem}")
        
        # Load receipt data
        receipt_data = load_receipt_data(receipt_file)
        if not receipt_data:
            errors.append(f"Failed to load {receipt_file.stem}")
            continue
        
        # Evaluate receipt
        result = await evaluate_single_receipt(receipt_data, receipt_file.stem)
        results.append(result)
        
        if result.error:
            errors.append(f"{receipt_file.stem}: {result.error}")
        
        # Log progress
        if result.line_items_detected > 0:
            avg_confidence = sum(result.confidence_scores) / len(result.confidence_scores)
            logger.info(f"  -> {result.line_items_detected} line items detected, "
                       f"avg confidence: {avg_confidence:.2f}, "
                       f"processing time: {result.processing_time_ms:.1f}ms")
        else:
            logger.info(f"  -> No line items detected")
    
    # Calculate aggregate metrics
    successful_results = [r for r in results if not r.error]
    total_receipts = len(receipt_files)
    successful_evaluations = len(successful_results)
    
    if not successful_results:
        return BaselineMetrics(
            total_receipts=total_receipts,
            successful_evaluations=0,
            total_words_processed=0,
            total_line_items_detected=0,
            average_line_items_per_receipt=0.0,
            average_processing_time_ms=0.0,
            average_confidence=0.0,
            horizontal_grouping_success_rate=0.0,
            error_rate=1.0,
            errors=errors
        )
    
    total_words_processed = sum(r.total_words for r in successful_results)
    total_line_items_detected = sum(r.line_items_detected for r in successful_results)
    total_processing_time = sum(r.processing_time_ms for r in successful_results)
    
    # Calculate confidence scores
    all_confidence_scores = []
    for r in successful_results:
        all_confidence_scores.extend(r.confidence_scores)
    
    average_confidence = (
        sum(all_confidence_scores) / len(all_confidence_scores)
        if all_confidence_scores else 0.0
    )
    
    # Calculate success rate (receipts with at least 1 line item detected)
    successful_detections = sum(1 for r in successful_results if r.line_items_detected > 0)
    horizontal_grouping_success_rate = successful_detections / successful_evaluations
    
    return BaselineMetrics(
        total_receipts=total_receipts,
        successful_evaluations=successful_evaluations,
        total_words_processed=total_words_processed,
        total_line_items_detected=total_line_items_detected,
        average_line_items_per_receipt=total_line_items_detected / successful_evaluations,
        average_processing_time_ms=total_processing_time / successful_evaluations,
        average_confidence=average_confidence,
        horizontal_grouping_success_rate=horizontal_grouping_success_rate,
        error_rate=len(errors) / total_receipts,
        errors=errors
    )


def print_baseline_report(metrics: BaselineMetrics):
    """Print a comprehensive baseline metrics report."""
    
    print("\n" + "="*80)
    print("HORIZONTAL GROUPING BASELINE METRICS REPORT")
    print("="*80)
    
    print(f"\n📊 DATASET OVERVIEW")
    print(f"   Total receipts processed: {metrics.total_receipts}")
    print(f"   Successful evaluations: {metrics.successful_evaluations}")
    print(f"   Error rate: {metrics.error_rate:.1%}")
    
    print(f"\n📝 WORD PROCESSING")
    print(f"   Total words processed: {metrics.total_words_processed:,}")
    print(f"   Average words per receipt: {metrics.total_words_processed / metrics.successful_evaluations:.1f}")
    
    print(f"\n🎯 LINE ITEM DETECTION")
    print(f"   Total line items detected: {metrics.total_line_items_detected}")
    print(f"   Average line items per receipt: {metrics.average_line_items_per_receipt:.1f}")
    print(f"   Horizontal grouping success rate: {metrics.horizontal_grouping_success_rate:.1%}")
    
    print(f"\n⚡ PERFORMANCE")
    print(f"   Average processing time: {metrics.average_processing_time_ms:.1f}ms per receipt")
    print(f"   Average confidence score: {metrics.average_confidence:.2f}")
    
    if metrics.total_line_items_detected > 0:
        print(f"\n💡 EFFICIENCY ESTIMATES")
        # Estimate LLM call reduction based on detected line items
        baseline_llm_calls_per_receipt = 3  # Assumption: without spatial detection
        estimated_llm_calls_per_receipt = max(0, baseline_llm_calls_per_receipt - metrics.average_line_items_per_receipt)
        llm_reduction = (baseline_llm_calls_per_receipt - estimated_llm_calls_per_receipt) / baseline_llm_calls_per_receipt
        
        print(f"   Estimated baseline LLM calls per receipt: {baseline_llm_calls_per_receipt}")
        print(f"   Estimated LLM calls with horizontal grouping: {estimated_llm_calls_per_receipt:.1f}")
        print(f"   Estimated LLM call reduction: {llm_reduction:.1%}")
        
        # Cost estimates (assuming $0.01 per LLM call)
        cost_per_llm_call = 0.01
        baseline_cost_per_receipt = baseline_llm_calls_per_receipt * cost_per_llm_call
        optimized_cost_per_receipt = estimated_llm_calls_per_receipt * cost_per_llm_call
        cost_savings = baseline_cost_per_receipt - optimized_cost_per_receipt
        
        print(f"   Estimated cost savings: ${cost_savings:.3f} per receipt ({cost_savings/baseline_cost_per_receipt:.1%} reduction)")
    
    if metrics.errors:
        print(f"\n❌ ERRORS ({len(metrics.errors)} total)")
        for error in metrics.errors[:5]:  # Show first 5 errors
            print(f"   {error}")
        if len(metrics.errors) > 5:
            print(f"   ... and {len(metrics.errors) - 5} more errors")
    
    print("\n" + "="*80)


async def main():
    """Main evaluation function."""
    
    # Set up paths
    current_dir = Path(__file__).parent.parent.parent
    data_dir = current_dir / "receipt_data_with_labels"
    
    if not data_dir.exists():
        logger.error(f"Data directory not found: {data_dir}")
        logger.info("Please run export_all_receipts.py first to download production data")
        return
    
    # Run evaluation
    max_receipts = int(os.environ.get("MAX_RECEIPTS", "20"))
    logger.info(f"Evaluating horizontal grouping on up to {max_receipts} receipts")
    
    metrics = await evaluate_baseline_metrics(data_dir, max_receipts)
    
    # Print report
    print_baseline_report(metrics)
    
    # Save results to file
    results_file = Path(__file__).parent / "baseline_results.json"
    results_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {
            "total_receipts": metrics.total_receipts,
            "successful_evaluations": metrics.successful_evaluations,
            "total_words_processed": metrics.total_words_processed,
            "total_line_items_detected": metrics.total_line_items_detected,
            "average_line_items_per_receipt": metrics.average_line_items_per_receipt,
            "average_processing_time_ms": metrics.average_processing_time_ms,
            "average_confidence": metrics.average_confidence,
            "horizontal_grouping_success_rate": metrics.horizontal_grouping_success_rate,
            "error_rate": metrics.error_rate
        },
        "errors": metrics.errors
    }
    
    with open(results_file, 'w') as f:
        json.dump(results_data, f, indent=2)
    
    logger.info(f"Results saved to: {results_file}")


if __name__ == "__main__":
    asyncio.run(main())