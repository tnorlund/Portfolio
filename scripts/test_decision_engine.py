#!/usr/bin/env python3
"""
Test harness for the receipt label decision engine using exported DynamoDB data.

This script allows you to:
1. Load exported receipt data from JSON files
2. Run pattern detection locally
3. Test the decision engine logic
4. Compare results with production decisions
5. Generate performance metrics
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.agent.decision_engine import DecisionEngine
from receipt_label.constants import CORE_LABELS
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from receipt_label.utils.client_manager import ClientManager, ClientConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LocalDecisionTester:
    """Test the decision engine with local data."""
    
    def __init__(self, data_dir: str, use_real_pinecone: bool = True):
        self.data_dir = Path(data_dir)
        self.decision_engine = DecisionEngine()
        self.pattern_orchestrator = ParallelPatternOrchestrator()
        self.use_real_pinecone = use_real_pinecone
        
        # Initialize client manager for Pinecone access
        if use_real_pinecone:
            try:
                self.client_manager = ClientManager(ClientConfig.from_env())
                self.pinecone_index = self.client_manager.pinecone
                logger.info("Using real Pinecone index")
            except Exception as e:
                logger.warning(f"Failed to initialize Pinecone: {e}")
                logger.warning("Falling back to pattern detection only")
                self.pinecone_index = None
        else:
            self.pinecone_index = None
        
        # Pattern orchestrator already has detectors initialized
        
        self.results = {
            "total_receipts": 0,
            "gpt_required": 0,
            "pattern_sufficient": 0,
            "missing_essential": defaultdict(int),
            "decision_reasons": defaultdict(int),
            "pattern_coverage": defaultdict(int),
            "processing_times": []
        }
    
    def load_receipt_data(self, receipt_dir: Path) -> Optional[Dict]:
        """Load receipt data from exported JSON files."""
        try:
            data = {}
            
            # Load words
            words_file = receipt_dir / "words.json"
            if words_file.exists():
                with open(words_file) as f:
                    data["words"] = json.load(f)
            else:
                logger.warning(f"No words.json found in {receipt_dir}")
                return None
            
            # Load lines
            lines_file = receipt_dir / "lines.json"
            if lines_file.exists():
                with open(lines_file) as f:
                    data["lines"] = json.load(f)
            
            # Load metadata
            metadata_file = receipt_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file) as f:
                    data["metadata"] = json.load(f)
            
            # Load existing labels if any
            labels_file = receipt_dir / "labels.json"
            if labels_file.exists():
                with open(labels_file) as f:
                    data["labels"] = json.load(f)
            
            return data
            
        except Exception as e:
            logger.error(f"Error loading data from {receipt_dir}: {e}")
            return None
    
    def run_pattern_detection(self, words: List[Dict], metadata: Optional[Dict] = None) -> Dict[int, Dict]:
        """Run pattern detection on receipt words."""
        start_time = datetime.now()
        
        # Convert word dicts to format expected by detectors
        word_objects = []
        for word in words:
            # Create bounding box from x, y, width, height
            x = word.get('x', 0)
            y = word.get('y', 0)
            width = word.get('width', 0)
            height = word.get('height', 0)
            
            # Create a mock word object with all required attributes
            class MockWord:
                def __init__(self, data):
                    self.word_id = data.get('word_id')
                    self.text = data.get('text', '')
                    self.x = data.get('x', 0)
                    self.y = data.get('y', 0)
                    self.width = data.get('width', 0)
                    self.height = data.get('height', 0)
                    self.bounding_box = {
                        'x': self.x,
                        'y': self.y,
                        'width': self.width,
                        'height': self.height
                    }
                    self.line_id = data.get('line_id')
                    self.confidence = data.get('confidence', 0)
                    self.is_noise = data.get('is_noise', False)
                    self.receipt_id = data.get('receipt_id')
                    self.image_id = data.get('image_id')
                
                def calculate_centroid(self):
                    """Calculate the centroid of the word's bounding box."""
                    return (
                        self.x + self.width / 2,
                        self.y + self.height / 2
                    )
            
            word_obj = MockWord(word)
            word_objects.append(word_obj)
        
        # Run pattern detection (async)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            pattern_results = loop.run_until_complete(
                self.pattern_orchestrator.detect_all_patterns(word_objects)
            )
        finally:
            loop.close()
        
        # Get merchant patterns from Pinecone if available
        merchant_patterns = {}
        if metadata and metadata.get('merchant_name') and self.pinecone_index:
            try:
                # Query Pinecone for similar merchant receipts
                merchant_name = metadata['merchant_name']
                
                # Create a query vector from merchant name
                # In production, this would use the same embedding model as the indexing
                query_text = f"merchant:{merchant_name}"
                
                # For now, we'll skip the actual Pinecone query since we need embeddings
                # This would normally be:
                # results = self.pinecone_index.query(
                #     vector=embed_text(query_text),
                #     top_k=100,
                #     filter={"merchant_name": merchant_name}
                # )
                logger.debug(f"Would query Pinecone for merchant: {merchant_name}")
                
            except Exception as e:
                logger.warning(f"Pinecone query failed: {e}")
        
        # Combine results
        labeled_words = {}
        
        # Process currency patterns
        currency_results = pattern_results.get('currency', [])
        if isinstance(currency_results, dict):
            currency_results = currency_results.get('detections', [])
        
        for match in currency_results:
            if hasattr(match, 'word') and hasattr(match.word, 'word_id'):
                word_id = match.word.word_id
                # Map pattern type to label
                label = match.pattern_type.name  # e.g., GRAND_TOTAL, TAX, etc.
                
                labeled_words[word_id] = {
                    'label': label,
                    'confidence': match.confidence,
                    'source': 'currency_detector'
                }
        
        # Process datetime patterns
        datetime_results = pattern_results.get('datetime', [])
        if isinstance(datetime_results, dict):
            datetime_results = datetime_results.get('detections', [])
        
        for match in datetime_results:
            if hasattr(match, 'word') and hasattr(match.word, 'word_id'):
                word_id = match.word.word_id
                # Map pattern type to label
                if match.pattern_type.name == 'DATE':
                    label = CORE_LABELS.get('DATE', 'DATE')
                elif match.pattern_type.name == 'TIME':
                    label = CORE_LABELS.get('TIME', 'TIME')
                else:
                    label = 'DATETIME'
                    
                labeled_words[word_id] = {
                    'label': label,
                    'confidence': match.confidence,
                    'source': 'datetime_detector'
                }
        
        # Process contact patterns
        contact_results = pattern_results.get('contact', [])
        if isinstance(contact_results, dict):
            contact_results = contact_results.get('detections', [])
        
        for match in contact_results:
            if hasattr(match, 'word') and hasattr(match.word, 'word_id'):
                word_id = match.word.word_id
                # Map pattern type to label
                if match.pattern_type.name == 'PHONE_NUMBER':
                    label = CORE_LABELS.get('PHONE_NUMBER', 'PHONE_NUMBER')
                elif match.pattern_type.name == 'WEBSITE':
                    label = CORE_LABELS.get('WEBSITE', 'WEBSITE')
                elif match.pattern_type.name == 'EMAIL':
                    label = 'EMAIL'
                else:
                    label = 'CONTACT'
                    
                labeled_words[word_id] = {
                    'label': label,
                    'confidence': match.confidence,
                    'source': 'contact_detector'
                }
        
        # Process quantity patterns
        quantity_results = pattern_results.get('quantity', [])
        if isinstance(quantity_results, dict):
            quantity_results = quantity_results.get('detections', [])
        
        for match in quantity_results:
            if hasattr(match, 'word') and hasattr(match.word, 'word_id'):
                word_id = match.word.word_id
                labeled_words[word_id] = {
                    'label': CORE_LABELS.get('QUANTITY', 'QUANTITY'),
                    'confidence': match.confidence,
                    'source': 'quantity_detector'
                }
        
        # Apply merchant patterns
        if merchant_patterns.get('patterns'):
            for word in words:
                word_id = word.get('word_id')
                text = word.get('text', '').upper()
                
                for pattern in merchant_patterns['patterns']:
                    if pattern['text'].upper() in text and word_id not in labeled_words:
                        labeled_words[word_id] = {
                            'label': pattern['label'],
                            'confidence': pattern['confidence'],
                            'source': 'merchant_pattern'
                        }
        
        # Add merchant name if in metadata
        if metadata and metadata.get('merchant_name'):
            # Find merchant name in words
            merchant_name = metadata['merchant_name'].upper()
            for word in words:
                if merchant_name in word.get('text', '').upper():
                    labeled_words[word['word_id']] = {
                        'label': CORE_LABELS.get('MERCHANT_NAME', 'MERCHANT_NAME'),
                        'confidence': 0.95,
                        'source': 'metadata_match'
                    }
        
        processing_time = (datetime.now() - start_time).total_seconds()
        self.results['processing_times'].append(processing_time)
        
        return labeled_words
    
    def test_receipt(self, receipt_dir: Path) -> Dict:
        """Test a single receipt through the decision engine."""
        logger.info(f"\nTesting receipt: {receipt_dir.name}")
        
        # Load data
        data = self.load_receipt_data(receipt_dir)
        if not data or not data.get('words'):
            logger.error(f"  → No valid data found")
            return {"error": "No data"}
        
        words = data['words']
        metadata = data.get('metadata', {})
        existing_labels = data.get('labels', [])
        
        logger.info(f"  → Loaded {len(words)} words")
        if metadata:
            logger.info(f"  → Merchant: {metadata.get('merchant_name', 'Unknown')}")
        if existing_labels:
            logger.info(f"  → Existing labels: {len(existing_labels)}")
        
        # Run pattern detection
        labeled_words = self.run_pattern_detection(words, metadata)
        logger.info(f"  → Pattern detection labeled {len(labeled_words)} words")
        
        # Track pattern coverage
        for label_info in labeled_words.values():
            self.results['pattern_coverage'][label_info['source']] += 1
        
        # Run decision engine
        should_call_gpt, reason, unlabeled_words = self.decision_engine.should_call_gpt(
            words, labeled_words, metadata
        )
        
        # Record results
        self.results['total_receipts'] += 1
        if should_call_gpt:
            self.results['gpt_required'] += 1
            self.results['decision_reasons'][reason] += 1
            
            # Check which essential labels are missing
            found_labels = set(info['label'] for info in labeled_words.values())
            for essential in DecisionEngine.ESSENTIAL_LABELS:
                if essential not in found_labels:
                    self.results['missing_essential'][essential] += 1
        else:
            self.results['pattern_sufficient'] += 1
        
        logger.info(f"  → Decision: {'GPT Required' if should_call_gpt else 'Patterns Sufficient'}")
        logger.info(f"  → Reason: {reason}")
        logger.info(f"  → Unlabeled words: {len(unlabeled_words)}")
        
        # Compare with existing labels if available
        if existing_labels:
            existing_label_map = {
                label.get('word_id'): label.get('label') 
                for label in existing_labels
            }
            
            matches = 0
            for word_id, label_info in labeled_words.items():
                if existing_label_map.get(word_id) == label_info['label']:
                    matches += 1
            
            accuracy = matches / len(labeled_words) if labeled_words else 0
            logger.info(f"  → Pattern accuracy vs existing: {accuracy:.2%}")
        
        return {
            "receipt_dir": receipt_dir.name,
            "word_count": len(words),
            "labeled_count": len(labeled_words),
            "gpt_required": should_call_gpt,
            "reason": reason,
            "unlabeled_count": len(unlabeled_words)
        }
    
    def test_all_receipts(self) -> Dict:
        """Test all receipts in the data directory."""
        receipt_results = []
        
        # Find all receipt directories
        receipt_dirs = sorted([
            d for d in self.data_dir.iterdir() 
            if d.is_dir() and d.name.startswith(('receipt_', 'image_'))
        ])
        
        logger.info(f"Found {len(receipt_dirs)} receipts to test")
        
        for receipt_dir in receipt_dirs:
            result = self.test_receipt(receipt_dir)
            receipt_results.append(result)
        
        return receipt_results
    
    def generate_report(self) -> str:
        """Generate a summary report of test results."""
        stats = self.decision_engine.get_statistics()
        
        report = [
            "\n" + "="*60,
            "DECISION ENGINE TEST REPORT",
            "="*60,
            f"\nTotal Receipts Tested: {self.results['total_receipts']}",
            f"GPT Required: {self.results['gpt_required']} ({stats.get('gpt_rate', 0):.1%})",
            f"Patterns Sufficient: {self.results['pattern_sufficient']} ({stats.get('pattern_rate', 0):.1%})",
            
            "\nDecision Reasons:",
        ]
        
        for reason, count in sorted(self.results['decision_reasons'].items(), 
                                   key=lambda x: x[1], reverse=True):
            report.append(f"  - {reason}: {count}")
        
        report.extend([
            "\nMissing Essential Labels:",
        ])
        
        for label, count in sorted(self.results['missing_essential'].items(),
                                  key=lambda x: x[1], reverse=True):
            report.append(f"  - {label}: {count} receipts")
        
        report.extend([
            "\nPattern Detection Coverage:",
        ])
        
        for source, count in sorted(self.results['pattern_coverage'].items(),
                                  key=lambda x: x[1], reverse=True):
            report.append(f"  - {source}: {count} labels")
        
        if self.results['processing_times']:
            avg_time = sum(self.results['processing_times']) / len(self.results['processing_times'])
            report.extend([
                f"\nAverage Pattern Detection Time: {avg_time:.3f}s",
                f"Total Processing Time: {sum(self.results['processing_times']):.3f}s"
            ])
        
        report.extend([
            "\nExpected Cost Savings:",
            f"  - Skip Rate: {stats.get('pattern_rate', 0):.1%}",
            f"  - Estimated Savings: ~{stats.get('pattern_rate', 0) * 0.7:.0%} reduction in GPT costs",
            "="*60
        ])
        
        return "\n".join(report)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test receipt label decision engine with exported data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test all receipts in export directory
  %(prog)s ./receipt_data
  
  # Test specific receipt
  %(prog)s ./receipt_data --receipt image_550e8400_receipt_00001
  
  # Generate detailed report
  %(prog)s ./receipt_data --report detailed
  
  # Test without Pinecone (pattern detection only)
  %(prog)s ./receipt_data --no-pinecone
        """
    )
    
    parser.add_argument("data_dir", help="Directory containing exported receipt data")
    parser.add_argument("--receipt", help="Test specific receipt directory")
    parser.add_argument("--no-pinecone", action="store_true", 
                       help="Disable Pinecone and use pattern detection only")
    parser.add_argument("--report", choices=["simple", "detailed"], 
                       default="simple", help="Report detail level")
    parser.add_argument("--output", help="Save report to file")
    
    args = parser.parse_args()
    
    # Validate data directory
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        logger.error(f"Data directory not found: {data_dir}")
        sys.exit(1)
    
    # Create tester
    use_pinecone = not args.no_pinecone
    tester = LocalDecisionTester(args.data_dir, use_real_pinecone=use_pinecone)
    
    # Run tests
    if args.receipt:
        # Test specific receipt
        receipt_dir = data_dir / args.receipt
        if not receipt_dir.exists():
            logger.error(f"Receipt directory not found: {receipt_dir}")
            sys.exit(1)
        
        result = tester.test_receipt(receipt_dir)
        print(json.dumps(result, indent=2))
    else:
        # Test all receipts
        results = tester.test_all_receipts()
        
        # Generate report
        report = tester.generate_report()
        print(report)
        
        # Save report if requested
        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
                if args.report == "detailed":
                    f.write("\n\nDetailed Results:\n")
                    json.dump(results, f, indent=2)
            logger.info(f"\nReport saved to: {args.output}")


if __name__ == "__main__":
    main()