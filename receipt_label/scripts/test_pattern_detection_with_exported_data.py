#!/usr/bin/env python3
"""
Test pattern detection enhancements with exported real data.

This script loads exported receipt data and tests the pattern detection
enhancements from Phase 2-3 to measure performance improvements.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Any

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import pattern detection modules
from receipt_label.pattern_detection.enhanced_orchestrator import (
    OptimizationLevel,
    detect_patterns_optimized,
    compare_optimization_performance
)
from receipt_dynamo.entities import ReceiptWord


def load_exported_image(json_file: Path) -> Dict[str, Any]:
    """Load an exported image JSON file."""
    with open(json_file, 'r') as f:
        return json.load(f)


def extract_receipt_words_from_image(image_data: Dict[str, Any]) -> List[ReceiptWord]:
    """Extract ReceiptWord objects from exported image data."""
    receipt_words = []
    
    for word_data in image_data.get('receipt_words', []):
        try:
            # Create ReceiptWord object from the exported data
            receipt_word = ReceiptWord(**word_data)
            receipt_words.append(receipt_word)
        except Exception as e:
            logging.debug(f"Skipping word due to error: {e}")
            continue
    
    return receipt_words


def get_merchant_name_from_image(image_data: Dict[str, Any]) -> str:
    """Extract merchant name from image metadata if available."""
    # Check receipt metadata
    for metadata in image_data.get('receipt_metadatas', []):
        if 'merchant_name' in metadata:
            return metadata['merchant_name']
    
    # Fallback to unknown
    return "Unknown"


async def test_single_image(json_file: Path, optimization_level: OptimizationLevel) -> Dict[str, Any]:
    """Test pattern detection on a single exported image."""
    logging.info(f"Testing image: {json_file.name}")
    
    # Load the exported data
    image_data = load_exported_image(json_file)
    
    # Extract receipt words
    receipt_words = extract_receipt_words_from_image(image_data)
    
    if not receipt_words:
        logging.warning(f"No receipt words found in {json_file.name}")
        return {
            'image_id': json_file.stem,
            'word_count': 0,
            'results': None,
            'error': 'No receipt words found'
        }
    
    # Get merchant name
    merchant_name = get_merchant_name_from_image(image_data)
    
    logging.info(f"Found {len(receipt_words)} words for merchant: {merchant_name}")
    
    try:
        # Test pattern detection
        start_time = time.time()
        if optimization_level == OptimizationLevel.ADVANCED:
            results = await detect_patterns_optimized(
                words=receipt_words,
                merchant_name=merchant_name
            )
        else:
            # Use the enhanced orchestrator directly for other optimization levels
            from receipt_label.pattern_detection.enhanced_orchestrator import EnhancedPatternOrchestrator
            orchestrator = EnhancedPatternOrchestrator(optimization_level)
            results = await orchestrator.detect_patterns(receipt_words, merchant_name)
        end_time = time.time()
        
        processing_time = end_time - start_time
        
        # Count labeled words (if available)
        labeled_words = 0
        try:
            labeled_words = sum(1 for word in receipt_words if hasattr(word, 'labels') and word.labels)
        except AttributeError:
            # Labels attribute doesn't exist, which is fine
            pass
        
        return {
            'image_id': json_file.stem,
            'merchant_name': merchant_name,
            'word_count': len(receipt_words),
            'labeled_words': labeled_words,
            'processing_time_ms': processing_time * 1000,
            'optimization_level': optimization_level.value,
            'pattern_matches': len(results.get('patterns', results.get('detected_patterns', []))),
            'results': results
        }
        
    except Exception as e:
        logging.error(f"Error testing {json_file.name}: {e}")
        return {
            'image_id': json_file.stem,
            'word_count': len(receipt_words),
            'error': str(e)
        }


async def test_optimization_comparison(json_file: Path) -> Dict[str, Any]:
    """Test all optimization levels and compare performance."""
    logging.info(f"Comparing optimization levels for: {json_file.name}")
    
    # Load the exported data
    image_data = load_exported_image(json_file)
    receipt_words = extract_receipt_words_from_image(image_data)
    
    if not receipt_words:
        return {
            'image_id': json_file.stem,
            'error': 'No receipt words found'
        }
    
    merchant_name = get_merchant_name_from_image(image_data)
    
    try:
        # Compare all optimization levels
        comparison = await compare_optimization_performance(
            words=receipt_words,
            merchant_name=merchant_name
        )
        
        return {
            'image_id': json_file.stem,
            'merchant_name': merchant_name,
            'word_count': len(receipt_words),
            'comparison': comparison
        }
        
    except Exception as e:
        logging.error(f"Error comparing optimization levels for {json_file.name}: {e}")
        return {
            'image_id': json_file.stem,
            'error': str(e)
        }


async def run_pattern_tests(data_dir: str, optimization_level: OptimizationLevel, 
                          limit: int = None, compare_all: bool = False) -> Dict[str, Any]:
    """Run pattern detection tests on all exported data."""
    data_path = Path(data_dir)
    
    if not data_path.exists():
        raise ValueError(f"Data directory does not exist: {data_dir}")
    
    # Find all JSON files
    json_files = list(data_path.glob("*.json"))
    
    if not json_files:
        raise ValueError(f"No JSON files found in {data_dir}")
    
    if limit:
        json_files = json_files[:limit]
    
    logging.info(f"Testing {len(json_files)} images with {optimization_level.value} optimization")
    
    results = []
    
    for i, json_file in enumerate(json_files):
        try:
            if compare_all:
                result = await test_optimization_comparison(json_file)
            else:
                result = await test_single_image(json_file, optimization_level)
                
            results.append(result)
            
            if (i + 1) % 5 == 0:
                logging.info(f"Processed {i + 1}/{len(json_files)} images")
                
        except Exception as e:
            logging.error(f"Error processing {json_file}: {e}")
            results.append({
                'image_id': json_file.stem,
                'error': str(e)
            })
    
    # Calculate summary statistics
    successful_tests = [r for r in results if 'error' not in r]
    
    if successful_tests and not compare_all:
        avg_processing_time = sum(r.get('processing_time_ms', 0) for r in successful_tests) / len(successful_tests)
        total_words = sum(r.get('word_count', 0) for r in successful_tests)
        total_labeled = sum(r.get('labeled_words', 0) for r in successful_tests)
        total_patterns = sum(r.get('pattern_matches', 0) for r in successful_tests)
        
        summary = {
            'total_images': len(json_files),
            'successful_tests': len(successful_tests),
            'failed_tests': len(json_files) - len(successful_tests),
            'avg_processing_time_ms': avg_processing_time,
            'total_words': total_words,
            'total_labeled_words': total_labeled,
            'total_pattern_matches': total_patterns,
            'optimization_level': optimization_level.value
        }
    else:
        summary = {
            'total_images': len(json_files),
            'successful_tests': len(successful_tests),
            'failed_tests': len(json_files) - len(successful_tests)
        }
    
    return {
        'summary': summary,
        'results': results
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Test pattern detection with exported data")
    parser.add_argument("data_dir", help="Directory containing exported JSON files")
    parser.add_argument("--optimization-level", choices=["legacy", "basic", "optimized", "advanced"],
                       default="advanced", help="Optimization level to test")
    parser.add_argument("--limit", type=int, help="Limit number of images to test")
    parser.add_argument("--compare-all", action="store_true",
                       help="Compare all optimization levels")
    parser.add_argument("--output", help="Output file for results (JSON)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    try:
        # Convert optimization level string to enum
        optimization_level = OptimizationLevel(args.optimization_level)
        
        # Run the tests
        start_time = time.time()
        results = asyncio.run(run_pattern_tests(
            data_dir=args.data_dir,
            optimization_level=optimization_level,
            limit=args.limit,
            compare_all=args.compare_all
        ))
        end_time = time.time()
        
        total_time = end_time - start_time
        
        # Print summary
        summary = results['summary']
        print(f"\nPattern Detection Test Results:")
        print(f"==============================")
        print(f"Total images: {summary['total_images']}")
        print(f"Successful tests: {summary['successful_tests']}")
        print(f"Failed tests: {summary['failed_tests']}")
        print(f"Total test time: {total_time:.2f}s")
        
        if not args.compare_all and summary['successful_tests'] > 0:
            print(f"Average processing time: {summary['avg_processing_time_ms']:.2f}ms")
            print(f"Total words processed: {summary['total_words']}")
            print(f"Total pattern matches: {summary['total_pattern_matches']}")
            print(f"Optimization level: {summary['optimization_level']}")
        
        # Save results if requested
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(exist_ok=True, parents=True)
            
            with open(output_path, 'w') as f:
                json.dump(results, f, indent=2, default=str)
            
            print(f"Results saved to: {args.output}")
        
    except KeyboardInterrupt:
        print("\nTesting cancelled by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Testing failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()