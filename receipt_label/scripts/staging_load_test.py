#!/usr/bin/env python3
"""
Staging load test for receipt processing system.

This script simulates a 30-day replay of receipt processing at 2x peak TPS
to validate system performance, cost, and accuracy under load.
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.monitoring.integration import MonitoredLabelingSystem
from receipt_label.agent.decision_engine import DecisionEngine, GPTDecision
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from receipt_label.monitoring.production_monitor import ProductionMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class LoadTestConfig:
    """Configuration for load testing."""
    # Test duration
    duration_days: int = 30
    
    # Target TPS (transactions per second)
    base_tps: float = 10.0  # Normal peak TPS
    multiplier: float = 2.0  # 2x multiplier
    
    # Batch size for concurrent processing
    batch_size: int = 100
    
    # Merchant distribution (based on production data)
    merchant_distribution: Dict[str, float] = field(default_factory=lambda: {
        "walmart": 0.25,
        "target": 0.15,
        "mcdonalds": 0.10,
        "starbucks": 0.08,
        "cvs": 0.07,
        "shell": 0.05,
        "kroger": 0.05,
        "other": 0.25
    })
    
    # Receipt complexity distribution
    complexity_distribution: Dict[str, float] = field(default_factory=lambda: {
        "simple": 0.60,    # <10 items, all essential labels
        "medium": 0.30,    # 10-30 items, most labels
        "complex": 0.10    # 30+ items, missing labels
    })
    
    # Time-based load patterns (hour of day -> multiplier)
    hourly_pattern: Dict[int, float] = field(default_factory=lambda: {
        0: 0.3, 1: 0.2, 2: 0.2, 3: 0.2, 4: 0.3, 5: 0.5,
        6: 0.7, 7: 0.9, 8: 1.0, 9: 1.0, 10: 1.0, 11: 1.2,
        12: 1.5, 13: 1.3, 14: 1.0, 15: 1.0, 16: 1.1, 17: 1.3,
        18: 1.5, 19: 1.2, 20: 1.0, 21: 0.8, 22: 0.6, 23: 0.4
    })
    
    # Output configuration
    output_dir: Path = Path("load_test_results")
    checkpoint_interval: int = 1000  # Save progress every N receipts


@dataclass
class LoadTestMetrics:
    """Metrics collected during load testing."""
    total_receipts: int = 0
    successful_receipts: int = 0
    failed_receipts: int = 0
    
    # Decision distribution
    skip_count: int = 0
    batch_count: int = 0
    required_count: int = 0
    
    # Performance metrics
    total_latency_ms: float = 0.0
    pattern_latency_ms: float = 0.0
    gpt_latency_ms: float = 0.0
    
    # Cost metrics
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    
    # Quality metrics
    false_skips: int = 0
    missing_labels: List[str] = field(default_factory=list)
    
    # Error tracking
    errors: List[Dict] = field(default_factory=list)
    
    def get_summary(self) -> Dict:
        """Get summary statistics."""
        if self.total_receipts == 0:
            return {}
        
        return {
            "total_receipts": self.total_receipts,
            "success_rate": self.successful_receipts / self.total_receipts,
            "decision_distribution": {
                "skip": self.skip_count / self.total_receipts,
                "batch": self.batch_count / self.total_receipts,
                "required": self.required_count / self.total_receipts,
            },
            "average_latency_ms": self.total_latency_ms / self.total_receipts,
            "average_cost_usd": self.total_cost_usd / self.total_receipts,
            "false_skip_rate": self.false_skips / self.skip_count if self.skip_count > 0 else 0,
            "error_rate": len(self.errors) / self.total_receipts,
        }


class ReceiptGenerator:
    """Generates synthetic receipt data for load testing."""
    
    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.receipt_counter = 0
    
    def generate_receipt(self, merchant: str, complexity: str) -> Tuple[List[Dict], Dict]:
        """Generate a synthetic receipt."""
        self.receipt_counter += 1
        
        # Base receipt structure
        receipt_id = f"load_test_{self.receipt_counter}"
        image_id = f"550e8400-e29b-41d4-a716-{self.receipt_counter:012d}"
        
        # Generate words based on complexity
        words = self._generate_words(merchant, complexity)
        
        # Metadata
        metadata = {
            "receipt_id": receipt_id,
            "image_id": image_id,
            "merchant_name": merchant if merchant != "other" else None,
            "complexity": complexity,
            "synthetic": True,
            "timestamp": datetime.now().isoformat()
        }
        
        return words, metadata
    
    def _generate_words(self, merchant: str, complexity: str) -> List[Dict]:
        """Generate receipt words based on merchant and complexity."""
        words = []
        word_id = 1
        line_id = 0
        
        # Header with merchant name
        if merchant != "other":
            words.append({
                "word_id": word_id,
                "text": merchant.upper(),
                "line_id": line_id,
                "confidence": 0.95
            })
            word_id += 1
            line_id += 1
        
        # Date
        words.extend([
            {"word_id": word_id, "text": "Date:", "line_id": line_id, "confidence": 0.9},
            {"word_id": word_id + 1, "text": "01/15/2024", "line_id": line_id, "confidence": 0.92}
        ])
        word_id += 2
        line_id += 1
        
        # Items based on complexity
        item_count = {
            "simple": random.randint(3, 8),
            "medium": random.randint(10, 25),
            "complex": random.randint(30, 50)
        }[complexity]
        
        for i in range(item_count):
            # Item name
            words.append({
                "word_id": word_id,
                "text": f"Item{i+1}",
                "line_id": line_id,
                "confidence": 0.85
            })
            word_id += 1
            
            # Price
            price = round(random.uniform(1.99, 49.99), 2)
            words.append({
                "word_id": word_id,
                "text": f"${price}",
                "line_id": line_id,
                "confidence": 0.9
            })
            word_id += 1
            line_id += 1
        
        # Subtotal (might be missing in complex receipts)
        if complexity != "complex" or random.random() > 0.3:
            words.extend([
                {"word_id": word_id, "text": "Subtotal:", "line_id": line_id, "confidence": 0.9},
                {"word_id": word_id + 1, "text": "$99.99", "line_id": line_id, "confidence": 0.88}
            ])
            word_id += 2
            line_id += 1
        
        # Tax
        words.extend([
            {"word_id": word_id, "text": "Tax:", "line_id": line_id, "confidence": 0.9},
            {"word_id": word_id + 1, "text": "$8.99", "line_id": line_id, "confidence": 0.87}
        ])
        word_id += 2
        line_id += 1
        
        # Total (might be unclear in complex receipts)
        if complexity == "complex" and random.random() < 0.2:
            # Ambiguous total
            words.extend([
                {"word_id": word_id, "text": "Amount", "line_id": line_id, "confidence": 0.7},
                {"word_id": word_id + 1, "text": "$108.98", "line_id": line_id, "confidence": 0.75}
            ])
        else:
            # Clear total
            words.extend([
                {"word_id": word_id, "text": "Total:", "line_id": line_id, "confidence": 0.92},
                {"word_id": word_id + 1, "text": "$108.98", "line_id": line_id, "confidence": 0.9}
            ])
        
        return words


class StagingLoadTest:
    """Main load test orchestrator."""
    
    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.metrics = LoadTestMetrics()
        self.generator = ReceiptGenerator(config)
        
        # Initialize system components
        self.decision_engine = DecisionEngine()
        self.pattern_orchestrator = ParallelPatternOrchestrator()
        self.monitored_system = MonitoredLabelingSystem(
            decision_engine=self.decision_engine,
            pattern_orchestrator=self.pattern_orchestrator,
            enable_ab_testing=False,
            enable_shadow_testing=True,  # Enable for quality metrics
            shadow_sampling_rate=0.05
        )
        
        # Ensure output directory exists
        self.config.output_dir.mkdir(exist_ok=True)
    
    async def run(self):
        """Run the load test."""
        logger.info(f"Starting {self.config.duration_days}-day load test at {self.config.multiplier}x TPS")
        
        start_time = time.time()
        target_tps = self.config.base_tps * self.config.multiplier
        
        # Calculate total receipts to process
        total_seconds = self.config.duration_days * 24 * 3600
        total_receipts = int(total_seconds * target_tps)
        
        logger.info(f"Target: {total_receipts:,} receipts at {target_tps} TPS")
        
        # Process in batches
        batch_tasks = []
        receipts_processed = 0
        
        # Simulate time progression
        simulated_time = datetime.now() - timedelta(days=self.config.duration_days)
        
        while receipts_processed < total_receipts:
            # Calculate current TPS based on time of day
            hour = simulated_time.hour
            hourly_multiplier = self.config.hourly_pattern.get(hour, 1.0)
            current_tps = target_tps * hourly_multiplier
            
            # Calculate batch size for this interval
            interval_seconds = 1.0  # Process in 1-second intervals
            receipts_in_interval = int(current_tps * interval_seconds)
            
            # Generate and process batch
            batch = []
            for _ in range(min(receipts_in_interval, total_receipts - receipts_processed)):
                # Select merchant and complexity
                merchant = self._select_merchant()
                complexity = self._select_complexity()
                
                # Generate receipt
                words, metadata = self.generator.generate_receipt(merchant, complexity)
                batch.append((words, metadata))
            
            # Process batch concurrently
            if batch:
                batch_start = time.time()
                results = await self._process_batch(batch)
                batch_duration = time.time() - batch_start
                
                # Update metrics
                self._update_metrics(results)
                receipts_processed += len(batch)
                
                # Log progress
                if receipts_processed % 1000 == 0:
                    elapsed = time.time() - start_time
                    rate = receipts_processed / elapsed
                    eta = (total_receipts - receipts_processed) / rate
                    
                    logger.info(
                        f"Progress: {receipts_processed:,}/{total_receipts:,} "
                        f"({receipts_processed/total_receipts:.1%}) "
                        f"Rate: {rate:.1f} RPS, ETA: {eta/60:.1f} min"
                    )
                    
                    # Save checkpoint
                    self._save_checkpoint(receipts_processed)
                
                # Sleep to maintain target rate
                sleep_time = max(0, interval_seconds - batch_duration)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            
            # Advance simulated time
            simulated_time += timedelta(seconds=interval_seconds)
        
        # Final results
        duration = time.time() - start_time
        self._save_final_results(duration)
        
        logger.info(f"Load test completed in {duration/60:.1f} minutes")
        logger.info(f"Final metrics: {json.dumps(self.metrics.get_summary(), indent=2)}")
    
    def _select_merchant(self) -> str:
        """Select merchant based on distribution."""
        rand = random.random()
        cumulative = 0.0
        
        for merchant, prob in self.config.merchant_distribution.items():
            cumulative += prob
            if rand < cumulative:
                return merchant
        
        return "other"
    
    def _select_complexity(self) -> str:
        """Select complexity based on distribution."""
        rand = random.random()
        cumulative = 0.0
        
        for complexity, prob in self.config.complexity_distribution.items():
            cumulative += prob
            if rand < cumulative:
                return complexity
        
        return "medium"
    
    async def _process_batch(self, batch: List[Tuple[List[Dict], Dict]]) -> List[Dict]:
        """Process a batch of receipts concurrently."""
        tasks = []
        
        for words, metadata in batch:
            task = asyncio.create_task(self._process_single_receipt(words, metadata))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error processing receipt: {result}")
                processed_results.append({
                    "success": False,
                    "error": str(result),
                    "metadata": batch[i][1]
                })
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def _process_single_receipt(self, words: List[Dict], metadata: Dict) -> Dict:
        """Process a single receipt."""
        start_time = time.time()
        
        try:
            # Process with monitoring
            result = await self.monitored_system.process_receipt_with_monitoring(
                receipt_words=words,
                receipt_metadata=metadata
            )
            
            # Calculate total time
            total_time = (time.time() - start_time) * 1000
            
            return {
                "success": True,
                "decision": result["gpt_decision"],
                "metrics": result["metrics"],
                "total_time_ms": total_time,
                "metadata": metadata
            }
            
        except Exception as e:
            logger.error(f"Failed to process receipt {metadata.get('receipt_id')}: {e}")
            return {
                "success": False,
                "error": str(e),
                "metadata": metadata
            }
    
    def _update_metrics(self, results: List[Dict]):
        """Update metrics with batch results."""
        for result in results:
            self.metrics.total_receipts += 1
            
            if result["success"]:
                self.metrics.successful_receipts += 1
                
                # Decision distribution
                decision = result["decision"]
                if decision == "SKIP":
                    self.metrics.skip_count += 1
                elif decision == "BATCH":
                    self.metrics.batch_count += 1
                elif decision == "REQUIRED":
                    self.metrics.required_count += 1
                
                # Performance metrics
                metrics = result["metrics"]
                self.metrics.total_latency_ms += result["total_time_ms"]
                self.metrics.pattern_latency_ms += metrics.get("pattern_time_ms", 0)
                self.metrics.gpt_latency_ms += metrics.get("gpt_time_ms", 0)
                
                # Cost metrics
                self.metrics.total_tokens += metrics.get("gpt_tokens_used", 0)
                self.metrics.total_cost_usd += metrics.get("estimated_cost_usd", 0)
                
            else:
                self.metrics.failed_receipts += 1
                self.metrics.errors.append({
                    "receipt_id": result["metadata"].get("receipt_id"),
                    "error": result.get("error"),
                    "timestamp": datetime.now().isoformat()
                })
    
    def _save_checkpoint(self, receipts_processed: int):
        """Save checkpoint for resumability."""
        checkpoint = {
            "receipts_processed": receipts_processed,
            "metrics": self.metrics.__dict__,
            "timestamp": datetime.now().isoformat()
        }
        
        checkpoint_file = self.config.output_dir / f"checkpoint_{receipts_processed}.json"
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint, f, indent=2)
    
    def _save_final_results(self, duration_seconds: float):
        """Save final load test results."""
        results = {
            "config": {
                "duration_days": self.config.duration_days,
                "target_tps": self.config.base_tps * self.config.multiplier,
                "total_receipts": self.metrics.total_receipts
            },
            "summary": self.metrics.get_summary(),
            "duration_seconds": duration_seconds,
            "actual_tps": self.metrics.total_receipts / duration_seconds,
            "detailed_metrics": self.metrics.__dict__,
            "timestamp": datetime.now().isoformat()
        }
        
        results_file = self.config.output_dir / "load_test_results.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        # Generate summary report
        self._generate_report(results)
    
    def _generate_report(self, results: Dict):
        """Generate human-readable report."""
        report_file = self.config.output_dir / "load_test_report.md"
        
        with open(report_file, 'w') as f:
            f.write("# Staging Load Test Report\n\n")
            f.write(f"Generated: {results['timestamp']}\n\n")
            
            f.write("## Test Configuration\n")
            f.write(f"- Duration: {results['config']['duration_days']} days\n")
            f.write(f"- Target TPS: {results['config']['target_tps']:.1f}\n")
            f.write(f"- Total Receipts: {results['config']['total_receipts']:,}\n\n")
            
            f.write("## Results Summary\n")
            summary = results['summary']
            f.write(f"- Success Rate: {summary['success_rate']:.1%}\n")
            f.write(f"- Actual TPS: {results['actual_tps']:.1f}\n")
            f.write(f"- Average Latency: {summary['average_latency_ms']:.1f}ms\n")
            f.write(f"- Average Cost: ${summary['average_cost_usd']:.4f}\n")
            f.write(f"- False Skip Rate: {summary.get('false_skip_rate', 0):.2%}\n\n")
            
            f.write("## Decision Distribution\n")
            dist = summary['decision_distribution']
            f.write(f"- SKIP: {dist['skip']:.1%}\n")
            f.write(f"- BATCH: {dist['batch']:.1%}\n")
            f.write(f"- REQUIRED: {dist['required']:.1%}\n\n")
            
            f.write("## Cost Analysis\n")
            total_cost = self.metrics.total_cost_usd
            f.write(f"- Total Cost: ${total_cost:.2f}\n")
            f.write(f"- Projected Monthly Cost: ${total_cost * 30 / results['config']['duration_days']:.2f}\n")
            f.write(f"- Cost per 1K receipts: ${total_cost * 1000 / self.metrics.total_receipts:.2f}\n")


async def main():
    """Main entry point."""
    # Parse command line arguments
    import argparse
    
    parser = argparse.ArgumentParser(description="Run staging load test")
    parser.add_argument("--days", type=int, default=30, help="Test duration in days")
    parser.add_argument("--tps", type=float, default=10.0, help="Base TPS")
    parser.add_argument("--multiplier", type=float, default=2.0, help="TPS multiplier")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size")
    parser.add_argument("--output-dir", type=str, default="load_test_results", help="Output directory")
    
    args = parser.parse_args()
    
    # Create configuration
    config = LoadTestConfig(
        duration_days=args.days,
        base_tps=args.tps,
        multiplier=args.multiplier,
        batch_size=args.batch_size,
        output_dir=Path(args.output_dir)
    )
    
    # Run load test
    load_test = StagingLoadTest(config)
    await load_test.run()


if __name__ == "__main__":
    asyncio.run(main())