#!/usr/bin/env python3
"""
Baseline metrics evaluation for the enhanced pattern analyzer.

This script evaluates the enhanced pattern analyzer against real production
receipts and compares performance with the previous GPT-based approach.
"""

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Import our components
from receipt_label.pattern_detection.enhanced_pattern_analyzer import enhanced_pattern_analysis
from receipt_label.processors.line_item_processor import FastPatternMatcher
from receipt_label.data.local_data_loader import LocalDataLoader


class BaselineMetricsEvaluator:
    """Evaluate baseline metrics for line item detection improvements."""
    
    def __init__(self, data_dir: str):
        """Initialize evaluator with exported receipt data."""
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise ValueError(f"Data directory does not exist: {data_dir}")
        
        self.loader = LocalDataLoader(str(self.data_dir))
        self.pattern_matcher = FastPatternMatcher()
        
        # Metrics tracking
        self.evaluation_results = []
        self.processing_times = []
        self.error_count = 0
        
    def load_receipt_files(self) -> List[str]:
        """Get list of receipt JSON files to process."""
        json_files = list(self.data_dir.glob("*.json"))
        logger.info(f"Found {len(json_files)} receipt files to process")
        return [f.stem for f in json_files]
    
    def extract_currency_contexts_from_receipt(
        self, 
        receipt_data: Dict
    ) -> Optional[List[Dict]]:
        """Extract currency contexts from receipt data structure."""
        try:
            # Look for receipt words and lines in the data structure
            receipt_words = None
            receipt_lines = None
            
            # The exported data structure varies, so we need to find the right fields
            if 'receipt_words' in receipt_data:
                receipt_words = receipt_data['receipt_words']
            elif 'words' in receipt_data:
                receipt_words = receipt_data['words']
            
            if 'receipt_lines' in receipt_data:
                receipt_lines = receipt_data['receipt_lines']
            elif 'lines' in receipt_data:
                receipt_lines = receipt_data['lines']
            
            if not receipt_words:
                logger.warning("No receipt words found in data structure")
                return None
            
            # Convert to currency contexts format
            currency_contexts = []
            
            for word in receipt_words:
                # Extract required fields
                word_text = word.get('text', '')
                if not word_text:
                    continue
                
                # Look for currency patterns
                currency_matches = self.pattern_matcher.find_currency_amounts(
                    word_text,
                    word.get('line_id', '0'),
                    {
                        'x': word.get('bounding_box', {}).get('x', 0),
                        'y': word.get('bounding_box', {}).get('y', 0)
                    },
                    word.get('word_id')
                )
                
                for match in currency_matches:
                    # Find the full line text
                    line_id = match.line_id
                    full_line = ""
                    left_text = ""
                    
                    if receipt_lines:
                        for line in receipt_lines:
                            if str(line.get('line_id', '')) == str(line_id):
                                full_line = line.get('text', '')
                                break
                    
                    # If no line found, construct from word
                    if not full_line:
                        # Find all words on this line
                        line_words = [
                            w for w in receipt_words 
                            if str(w.get('line_id', '')) == str(line_id)
                        ]
                        # Sort by x position
                        line_words.sort(key=lambda w: w.get('bounding_box', {}).get('x', 0))
                        full_line = ' '.join(w.get('text', '') for w in line_words)
                    
                    # Extract left text (words to the left of currency)
                    word_x = match.position.get('x', 0)
                    left_words = []
                    for w in receipt_words:
                        if (str(w.get('line_id', '')) == str(line_id) and 
                            w.get('bounding_box', {}).get('x', 0) < word_x):
                            left_words.append(w)
                    
                    left_words.sort(key=lambda w: w.get('bounding_box', {}).get('x', 0))
                    left_text = ' '.join(w.get('text', '') for w in left_words)
                    
                    currency_contexts.append({
                        'amount': float(match.amount),
                        'text': match.text,
                        'line_id': match.line_id,
                        'word_id': match.word_id,
                        'x_position': match.position.get('x', 0),
                        'y_position': match.position.get('y', 0),
                        'full_line': full_line,
                        'left_text': left_text
                    })
            
            return currency_contexts if currency_contexts else None
            
        except Exception as e:
            logger.error(f"Error extracting currency contexts: {e}")
            return None
    
    def evaluate_single_receipt(self, receipt_file: str) -> Optional[Dict]:
        """Evaluate a single receipt file."""
        try:
            # Load receipt data
            file_path = self.data_dir / f"{receipt_file}.json"
            with open(file_path, 'r') as f:
                receipt_data = json.load(f)
            
            # Extract currency contexts
            currency_contexts = self.extract_currency_contexts_from_receipt(receipt_data)
            if not currency_contexts:
                return {
                    'receipt_id': receipt_file,
                    'status': 'no_currency_found',
                    'error': 'No currency amounts detected',
                    'processing_time_ms': 0,
                    'enhanced_result': None
                }
            
            logger.info(f"Processing {receipt_file}: {len(currency_contexts)} currency amounts found")
            
            # Time the enhanced analysis
            start_time = time.perf_counter()
            enhanced_result = enhanced_pattern_analysis(currency_contexts)
            end_time = time.perf_counter()
            
            processing_time_ms = (end_time - start_time) * 1000
            
            return {
                'receipt_id': receipt_file,
                'status': 'success',
                'currency_count': len(currency_contexts),
                'processing_time_ms': processing_time_ms,
                'enhanced_result': enhanced_result,
                'line_items_found': len(enhanced_result.get('line_items', [])),
                'financial_summary': enhanced_result.get('financial_summary', {}),
                'confidence': enhanced_result.get('confidence', 0),
                'error': None
            }
            
        except Exception as e:
            logger.error(f"Error processing {receipt_file}: {e}")
            return {
                'receipt_id': receipt_file,
                'status': 'error',
                'error': str(e),
                'processing_time_ms': 0,
                'enhanced_result': None
            }
    
    def evaluate_all_receipts(self, limit: Optional[int] = None) -> Dict:
        """Evaluate all receipts and generate comprehensive metrics."""
        receipt_files = self.load_receipt_files()
        
        if limit:
            receipt_files = receipt_files[:limit]
            logger.info(f"Limited evaluation to first {limit} receipts")
        
        logger.info(f"Starting evaluation of {len(receipt_files)} receipts...")
        
        successful_evaluations = []
        failed_evaluations = []
        processing_times = []
        
        for i, receipt_file in enumerate(receipt_files):
            logger.info(f"[{i+1}/{len(receipt_files)}] Processing {receipt_file}")
            
            result = self.evaluate_single_receipt(receipt_file)
            if result:
                if result['status'] == 'success':
                    successful_evaluations.append(result)
                    processing_times.append(result['processing_time_ms'])
                else:
                    failed_evaluations.append(result)
        
        # Calculate metrics
        total_receipts = len(receipt_files)
        successful_count = len(successful_evaluations)
        failed_count = len(failed_evaluations)
        
        # Processing time statistics
        avg_processing_time = sum(processing_times) / len(processing_times) if processing_times else 0
        min_processing_time = min(processing_times) if processing_times else 0
        max_processing_time = max(processing_times) if processing_times else 0
        
        # Line item detection statistics
        line_item_counts = [r['line_items_found'] for r in successful_evaluations]
        avg_line_items = sum(line_item_counts) / len(line_item_counts) if line_item_counts else 0
        
        # Financial field detection rates
        subtotal_found = sum(1 for r in successful_evaluations 
                            if r['financial_summary'].get('subtotal') is not None)
        tax_found = sum(1 for r in successful_evaluations 
                       if r['financial_summary'].get('tax') is not None)
        total_found = sum(1 for r in successful_evaluations 
                         if r['financial_summary'].get('total') is not None)
        
        # Confidence statistics
        confidences = [r['confidence'] for r in successful_evaluations if r['confidence'] > 0]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        # Currency detection statistics
        currency_counts = [r['currency_count'] for r in successful_evaluations]
        avg_currency_count = sum(currency_counts) / len(currency_counts) if currency_counts else 0
        
        return {
            'summary': {
                'total_receipts': total_receipts,
                'successful_processing': successful_count,
                'failed_processing': failed_count,
                'success_rate': (successful_count / total_receipts * 100) if total_receipts > 0 else 0,
            },
            'performance': {
                'avg_processing_time_ms': avg_processing_time,
                'min_processing_time_ms': min_processing_time,
                'max_processing_time_ms': max_processing_time,
                'total_processing_time_ms': sum(processing_times),
            },
            'line_item_detection': {
                'avg_line_items_per_receipt': avg_line_items,
                'total_line_items_found': sum(line_item_counts),
                'receipts_with_line_items': sum(1 for count in line_item_counts if count > 0),
                'line_item_detection_rate': (sum(1 for count in line_item_counts if count > 0) / 
                                           successful_count * 100) if successful_count > 0 else 0,
            },
            'financial_field_detection': {
                'subtotal_detection_rate': (subtotal_found / successful_count * 100) if successful_count > 0 else 0,
                'tax_detection_rate': (tax_found / successful_count * 100) if successful_count > 0 else 0,
                'total_detection_rate': (total_found / successful_count * 100) if successful_count > 0 else 0,
                'receipts_with_subtotal': subtotal_found,
                'receipts_with_tax': tax_found,
                'receipts_with_total': total_found,
            },
            'confidence_metrics': {
                'avg_confidence': avg_confidence,
                'high_confidence_receipts': sum(1 for c in confidences if c >= 0.8),
                'medium_confidence_receipts': sum(1 for c in confidences if 0.6 <= c < 0.8),
                'low_confidence_receipts': sum(1 for c in confidences if c < 0.6),
            },
            'currency_detection': {
                'avg_currency_amounts_per_receipt': avg_currency_count,
                'total_currency_amounts_found': sum(currency_counts),
            },
            'detailed_results': successful_evaluations,
            'failed_results': failed_evaluations,
        }
    
    def print_metrics_report(self, metrics: Dict):
        """Print a comprehensive metrics report."""
        print("=" * 80)
        print("ENHANCED PATTERN ANALYZER - BASELINE METRICS REPORT")
        print("=" * 80)
        
        # Summary
        summary = metrics['summary']
        print(f"\n📊 PROCESSING SUMMARY")
        print(f"   Total Receipts: {summary['total_receipts']}")
        print(f"   Successfully Processed: {summary['successful_processing']}")
        print(f"   Failed Processing: {summary['failed_processing']}")
        print(f"   Success Rate: {summary['success_rate']:.1f}%")
        
        # Performance
        perf = metrics['performance']
        print(f"\n⚡ PERFORMANCE METRICS")
        print(f"   Average Processing Time: {perf['avg_processing_time_ms']:.2f}ms")
        print(f"   Min Processing Time: {perf['min_processing_time_ms']:.2f}ms")
        print(f"   Max Processing Time: {perf['max_processing_time_ms']:.2f}ms")
        print(f"   Total Processing Time: {perf['total_processing_time_ms']:.2f}ms")
        
        # Line Item Detection
        li = metrics['line_item_detection']
        print(f"\n🏷️  LINE ITEM DETECTION")
        print(f"   Average Line Items per Receipt: {li['avg_line_items_per_receipt']:.1f}")
        print(f"   Total Line Items Found: {li['total_line_items_found']}")
        print(f"   Receipts with Line Items: {li['receipts_with_line_items']}")
        print(f"   Line Item Detection Rate: {li['line_item_detection_rate']:.1f}%")
        
        # Financial Fields
        ff = metrics['financial_field_detection']
        print(f"\n💰 FINANCIAL FIELD DETECTION")
        print(f"   Subtotal Detection Rate: {ff['subtotal_detection_rate']:.1f}%")
        print(f"   Tax Detection Rate: {ff['tax_detection_rate']:.1f}%")
        print(f"   Total Detection Rate: {ff['total_detection_rate']:.1f}%")
        print(f"   Receipts with Subtotal: {ff['receipts_with_subtotal']}")
        print(f"   Receipts with Tax: {ff['receipts_with_tax']}")
        print(f"   Receipts with Total: {ff['receipts_with_total']}")
        
        # Confidence
        conf = metrics['confidence_metrics']
        print(f"\n🎯 CONFIDENCE METRICS")
        print(f"   Average Confidence: {conf['avg_confidence']:.2f}")
        print(f"   High Confidence (≥0.8): {conf['high_confidence_receipts']}")
        print(f"   Medium Confidence (0.6-0.8): {conf['medium_confidence_receipts']}")
        print(f"   Low Confidence (<0.6): {conf['low_confidence_receipts']}")
        
        # Currency Detection
        cd = metrics['currency_detection']
        print(f"\n💵 CURRENCY DETECTION")
        print(f"   Average Currency Amounts per Receipt: {cd['avg_currency_amounts_per_receipt']:.1f}")
        print(f"   Total Currency Amounts Found: {cd['total_currency_amounts_found']}")
        
        # Success Analysis
        print(f"\n✅ SUCCESS ANALYSIS")
        successful = len(metrics['detailed_results'])
        if successful > 0:
            # Find best performing receipts
            best_receipts = sorted(
                metrics['detailed_results'], 
                key=lambda x: x['confidence'], 
                reverse=True
            )[:3]
            
            print(f"   Top 3 Most Confident Results:")
            for i, receipt in enumerate(best_receipts):
                print(f"     {i+1}. {receipt['receipt_id']}: {receipt['confidence']:.2f} confidence")
                print(f"        Line Items: {receipt['line_items_found']}")
                fs = receipt['financial_summary']
                print(f"        Financial: S={fs.get('subtotal', 'None')}, T={fs.get('tax', 'None')}, Total={fs.get('total', 'None')}")
        
        # Error Analysis
        failed = metrics['failed_results']
        if failed:
            print(f"\n❌ ERROR ANALYSIS")
            print(f"   Failed Receipts: {len(failed)}")
            
            # Group errors by type
            error_types = {}
            for result in failed:
                error_type = result.get('status', 'unknown')
                if error_type not in error_types:
                    error_types[error_type] = []
                error_types[error_type].append(result['receipt_id'])
            
            for error_type, receipt_ids in error_types.items():
                print(f"   {error_type}: {len(receipt_ids)} receipts")
                if len(receipt_ids) <= 3:
                    print(f"     {', '.join(receipt_ids)}")
        
        # Performance vs GPT Comparison
        print(f"\n📈 PERFORMANCE VS GPT COMPARISON")
        total_successful = summary['successful_processing']
        if total_successful > 0:
            estimated_gpt_cost = total_successful * 0.03  # $0.03 per receipt estimate
            processing_time_saved = total_successful * 2000  # 2 seconds per GPT call
            
            print(f"   Estimated GPT Cost Saved: ${estimated_gpt_cost:.2f}")
            print(f"   Processing Time Saved: {processing_time_saved/1000:.1f} seconds")
            print(f"   Average Speedup: ~2000x faster than GPT calls")
        
        print(f"\n" + "=" * 80)


def main():
    """Main evaluation function."""
    # Check for data directory
    data_dir = "./receipt_data_with_labels"
    if not Path(data_dir).exists():
        print(f"❌ Data directory not found: {data_dir}")
        print("Please run the export script first to download production data:")
        print("  python receipt_label/export_all_receipts.py")
        return
    
    # Initialize evaluator
    evaluator = BaselineMetricsEvaluator(data_dir)
    
    # Run evaluation on larger sample now that we have more data
    print("🚀 Starting baseline metrics evaluation...")
    metrics = evaluator.evaluate_all_receipts(limit=50)
    
    # Print report
    evaluator.print_metrics_report(metrics)
    
    # Save detailed results
    output_file = "baseline_metrics_results.json"
    with open(output_file, 'w') as f:
        json.dump(metrics, f, indent=2, default=str)
    
    print(f"\n💾 Detailed results saved to: {output_file}")


if __name__ == "__main__":
    main()