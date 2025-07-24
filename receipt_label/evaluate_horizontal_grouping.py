#!/usr/bin/env python3
"""
Simple evaluation script for horizontal grouping line item detection.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.geometry_utils import (
    is_horizontally_aligned_group,
    group_words_into_line_items
)
from receipt_label.spatial.horizontal_line_item_detector import (
    HorizontalLineItemDetector,
    HorizontalGroupingConfig
)

# Import the test helper
import sys
sys.path.append('tests/spatial')
from test_geometry_utils import create_receipt_word

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
            
            # Create ReceiptWord using the test helper
            word = create_receipt_word(
                text=word_data.get('text', ''),
                bounding_box=bbox,
                word_id=i + 1,
                confidence=word_data.get('confidence', 1.0)
            )
            receipt_words.append(word)
            
        except Exception as e:
            logger.warning(f"Error converting word {i}: {e}")
            continue
    
    return receipt_words


async def evaluate_receipt(receipt_file: Path) -> Dict:
    """Evaluate horizontal grouping on a single receipt."""
    
    logger.info(f"Evaluating {receipt_file.stem}")
    start_time = time.time()
    
    try:
        # Load receipt data
        receipt_data = load_receipt_data(receipt_file)
        if not receipt_data:
            return {
                'receipt_id': receipt_file.stem,
                'error': 'Failed to load receipt data'
            }
        
        # Extract words
        words_data = receipt_data.get('words', [])
        if not words_data:
            return {
                'receipt_id': receipt_file.stem,
                'error': 'No words found in receipt data'
            }
        
        # Convert to ReceiptWord objects
        receipt_words = convert_to_receipt_words(words_data)
        if not receipt_words:
            return {
                'receipt_id': receipt_file.stem,
                'error': 'Failed to convert words'
            }
        
        # Test horizontal grouping
        horizontal_groups = group_words_into_line_items(receipt_words)
        
        # Test detector
        detector = HorizontalLineItemDetector(
            config=HorizontalGroupingConfig(
                y_tolerance=0.02,
                min_confidence=0.3,
                x_gap_threshold=0.8
            )
        )
        
        line_items = detector.detect_line_items(receipt_words)
        
        # Calculate metrics
        processing_time = (time.time() - start_time) * 1000
        
        result = {
            'receipt_id': receipt_file.stem,
            'total_words': len(receipt_words),
            'horizontal_groups': len(horizontal_groups),
            'line_items_detected': len(line_items),
            'processing_time_ms': processing_time,
            'confidence_scores': [item.confidence for item in line_items],
            'detection_methods': [item.detection_method for item in line_items]
        }
        
        logger.info(f"  -> {result['line_items_detected']} line items, "
                   f"{result['processing_time_ms']:.1f}ms")
        
        return result
        
    except Exception as e:
        return {
            'receipt_id': receipt_file.stem,
            'error': str(e)
        }


async def main():
    """Main evaluation function."""
    
    # Find receipt data
    data_dir = Path("receipt_data_with_labels")
    if not data_dir.exists():
        logger.error(f"Data directory not found: {data_dir}")
        return
    
    # Get receipt files (limit to 10 for quick test)
    receipt_files = list(data_dir.glob("*.json"))[:10]
    logger.info(f"Found {len(receipt_files)} receipt files")
    
    if not receipt_files:
        logger.error("No receipt files found!")
        return
    
    # Evaluate receipts
    results = []
    for receipt_file in receipt_files:
        result = await evaluate_receipt(receipt_file)
        results.append(result)
    
    # Calculate summary metrics
    successful_results = [r for r in results if 'error' not in r]
    
    if not successful_results:
        logger.error("No successful evaluations!")
        return
    
    total_words = sum(r['total_words'] for r in successful_results)
    total_line_items = sum(r['line_items_detected'] for r in successful_results)
    total_time = sum(r['processing_time_ms'] for r in successful_results)
    
    # Print summary
    print("\n" + "="*60)
    print("HORIZONTAL GROUPING EVALUATION RESULTS")
    print("="*60)
    print(f"Receipts processed: {len(successful_results)}/{len(results)}")
    print(f"Total words: {total_words:,}")
    print(f"Total line items detected: {total_line_items}")
    print(f"Average line items per receipt: {total_line_items / len(successful_results):.1f}")
    print(f"Average processing time: {total_time / len(successful_results):.1f}ms")
    
    # Success rate
    receipts_with_line_items = sum(1 for r in successful_results if r['line_items_detected'] > 0)
    success_rate = receipts_with_line_items / len(successful_results)
    print(f"Success rate (receipts with ≥1 line item): {success_rate:.1%}")
    
    # Show detailed results
    print(f"\nDETAILED RESULTS:")
    for result in successful_results:
        if result['line_items_detected'] > 0:
            avg_conf = sum(result['confidence_scores']) / len(result['confidence_scores'])
            print(f"  {result['receipt_id']}: {result['line_items_detected']} items, "
                  f"confidence {avg_conf:.2f}")
        else:
            print(f"  {result['receipt_id']}: 0 items detected")
    
    # Show errors
    errors = [r for r in results if 'error' in r]
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for error in errors:
            print(f"  {error['receipt_id']}: {error['error']}")
    
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())