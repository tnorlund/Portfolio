#!/usr/bin/env python3
"""
Test pattern detection enhancements against local receipt dataset.

This script loads local receipt data and runs comprehensive pattern detection tests
to demonstrate the improvements from Phase 2-3 enhancements including:
- Centralized pattern configuration
- Selective detector invocation 
- Batch regex evaluation
- True CPU parallelism
- Trie-based multi-word detection
- Optimized keyword lookups
- Merchant-specific patterns
"""

import asyncio
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict, Counter
from dataclasses import asdict

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_label.data.local_data_loader import LocalDataLoader
from receipt_label.pattern_detection.enhanced_orchestrator import (
    EnhancedPatternOrchestrator,
    OptimizationLevel,
    detect_patterns_optimized,
    compare_optimization_performance
)
from receipt_label.pattern_detection.unified_pattern_engine import UNIFIED_PATTERN_ENGINE
from receipt_label.pattern_detection.parallel_engine import OPTIMIZED_PATTERN_DETECTOR
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator


class PatternDetectionTester:
    """Test pattern detection enhancements against local data."""
    
    def __init__(self, data_dir: str = "./receipt_data", output_dir: str = "./test_results"):
        """
        Initialize the tester.
        
        Args:
            data_dir: Directory containing local receipt data
            output_dir: Directory to save test results
        """
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        if not self.data_dir.exists():
            raise ValueError(f"Data directory does not exist: {data_dir}")
        
        self.data_loader = LocalDataLoader(str(self.data_dir))
        self.results = {}
        self.performance_stats = defaultdict(list)
        
        logging.info(f"Initialized tester with data from: {self.data_dir}")
    
    async def run_comprehensive_test(self, max_receipts: Optional[int] = None) -> Dict[str, Any]:
        """
        Run comprehensive pattern detection tests.
        
        Args:
            max_receipts: Maximum number of receipts to test (None for all)
            
        Returns:
            Test results summary
        """
        logging.info("Starting comprehensive pattern detection test")
        
        # Get available receipts
        available_receipts = self.data_loader.list_available_receipts()
        if not available_receipts:
            raise ValueError("No receipts found in data directory")
        
        if max_receipts:
            available_receipts = available_receipts[:max_receipts]
        
        logging.info(f"Testing {len(available_receipts)} receipts")
        
        # Test results
        test_results = {
            "metadata": {
                "test_started": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_receipts": len(available_receipts),
                "data_directory": str(self.data_dir),
                "output_directory": str(self.output_dir)
            },
            "performance_comparison": {},
            "optimization_analysis": {},
            "pattern_coverage": {},
            "merchant_analysis": {},
            "detailed_results": []
        }
        
        # Run performance comparison tests
        logging.info("Running performance comparison tests...")
        test_results["performance_comparison"] = await self._run_performance_comparison(available_receipts)
        
        # Run optimization analysis
        logging.info("Running optimization analysis...")
        test_results["optimization_analysis"] = await self._run_optimization_analysis(available_receipts)
        
        # Run pattern coverage analysis
        logging.info("Running pattern coverage analysis...")
        test_results["pattern_coverage"] = await self._run_pattern_coverage_analysis(available_receipts)
        
        # Run merchant-specific analysis
        logging.info("Running merchant-specific analysis...")
        test_results["merchant_analysis"] = await self._run_merchant_analysis(available_receipts)
        
        # Save results
        test_results["metadata"]["test_completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save_results(test_results)
        
        logging.info(f"Comprehensive test completed. Results saved to: {self.output_dir}")
        return test_results
    
    async def _run_performance_comparison(self, receipts: List[tuple]) -> Dict[str, Any]:
        """Run performance comparison between optimization levels."""
        logging.info("Comparing performance across optimization levels")
        
        # Sample a subset for performance testing
        test_receipts = receipts[:min(10, len(receipts))]
        
        performance_results = {
            "sample_size": len(test_receipts),
            "optimization_levels": {},
            "summary": {}
        }
        
        optimization_levels = [
            OptimizationLevel.LEGACY,
            OptimizationLevel.BASIC,
            OptimizationLevel.OPTIMIZED,
            OptimizationLevel.ADVANCED
        ]
        
        for level in optimization_levels:
            logging.info(f"Testing optimization level: {level.value}")
            level_results = await self._test_optimization_level(test_receipts, level)
            performance_results["optimization_levels"][level.value] = level_results
        
        # Calculate summary statistics
        performance_results["summary"] = self._calculate_performance_summary(
            performance_results["optimization_levels"]
        )
        
        return performance_results
    
    async def _test_optimization_level(self, receipts: List[tuple], level: OptimizationLevel) -> Dict[str, Any]:
        """Test a specific optimization level."""
        orchestrator = EnhancedPatternOrchestrator(level)
        
        results = {
            "level": level.value,
            "total_processing_time_ms": 0,
            "average_processing_time_ms": 0,
            "total_patterns_detected": 0,
            "receipts_processed": 0,
            "errors": [],
            "per_receipt_stats": []
        }
        
        start_time = time.time()
        
        for image_id, receipt_id in receipts:
            try:
                # Load receipt data
                receipt_data = self.data_loader.load_receipt_by_id(image_id, receipt_id)
                if not receipt_data:
                    results["errors"].append(f"Failed to load receipt {image_id}/{receipt_id}")
                    continue
                
                receipt, words, lines = receipt_data
                
                # Test pattern detection
                detection_start = time.time()
                detection_result = await orchestrator.detect_patterns(words)
                detection_time = (time.time() - detection_start) * 1000
                
                # Record stats
                patterns_found = detection_result.get("performance_metrics", {}).get("patterns_detected", 0)
                
                results["per_receipt_stats"].append({
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "processing_time_ms": detection_time,
                    "patterns_detected": patterns_found,
                    "words_processed": len([w for w in words if not w.is_noise])
                })
                
                results["receipts_processed"] += 1
                results["total_patterns_detected"] += patterns_found
                
            except Exception as e:
                error_msg = f"Error processing receipt {image_id}/{receipt_id}: {e}"
                logging.error(error_msg)
                results["errors"].append(error_msg)
        
        total_time = (time.time() - start_time) * 1000
        results["total_processing_time_ms"] = total_time
        
        if results["receipts_processed"] > 0:
            results["average_processing_time_ms"] = total_time / results["receipts_processed"]
        
        return results
    
    def _calculate_performance_summary(self, level_results: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate performance improvement summary."""
        if "legacy" not in level_results or "advanced" not in level_results:
            return {"error": "Missing baseline or advanced results"}
        
        legacy = level_results["legacy"]
        advanced = level_results["advanced"]
        
        if legacy["receipts_processed"] == 0 or advanced["receipts_processed"] == 0:
            return {"error": "No successful processing in comparison"}
        
        legacy_avg = legacy["average_processing_time_ms"]
        advanced_avg = advanced["average_processing_time_ms"]
        
        time_improvement = ((legacy_avg - advanced_avg) / legacy_avg) * 100 if legacy_avg > 0 else 0
        speedup_factor = legacy_avg / advanced_avg if advanced_avg > 0 else 1.0
        
        return {
            "time_improvement_percent": time_improvement,
            "speedup_factor": speedup_factor,
            "legacy_avg_ms": legacy_avg,
            "advanced_avg_ms": advanced_avg,
            "patterns_improvement": {
                "legacy_total": legacy["total_patterns_detected"],
                "advanced_total": advanced["total_patterns_detected"],
                "improvement": advanced["total_patterns_detected"] - legacy["total_patterns_detected"]
            }
        }
    
    async def _run_optimization_analysis(self, receipts: List[tuple]) -> Dict[str, Any]:
        """Analyze specific optimizations and their impact."""
        logging.info("Analyzing optimization impact")
        
        # Test subset
        test_receipts = receipts[:min(5, len(receipts))]
        
        optimization_analysis = {
            "sample_size": len(test_receipts),
            "optimizations_tested": {},
            "feature_analysis": {}
        }
        
        for image_id, receipt_id in test_receipts:
            try:
                receipt_data = self.data_loader.load_receipt_by_id(image_id, receipt_id)
                if not receipt_data:
                    continue
                
                receipt, words, lines = receipt_data
                
                # Test with performance comparison mode
                comparison_result = await compare_optimization_performance(words)
                
                if "comparison" in comparison_result:
                    comparison = comparison_result["comparison"]
                    optimization_analysis["optimizations_tested"][f"{image_id}_{receipt_id}"] = {
                        "legacy_time_ms": comparison["legacy"]["processing_time_ms"],
                        "advanced_time_ms": comparison["advanced"]["processing_time_ms"],
                        "improvement": comparison["performance_improvement"],
                        "legacy_patterns": comparison["legacy"]["patterns_found"],
                        "advanced_patterns": comparison["advanced"]["patterns_found"]
                    }
                
            except Exception as e:
                logging.error(f"Error in optimization analysis for {image_id}/{receipt_id}: {e}")
        
        # Analyze specific features
        optimization_analysis["feature_analysis"] = await self._analyze_specific_features(test_receipts)
        
        return optimization_analysis
    
    async def _analyze_specific_features(self, receipts: List[tuple]) -> Dict[str, Any]:
        """Analyze specific enhanced features."""
        feature_analysis = {
            "trie_based_detection": {"total_matches": 0, "multi_word_matches": 0},
            "merchant_patterns": {"total_matches": 0, "merchant_specific": 0},
            "automaton_keywords": {"total_matches": 0, "phrase_matches": 0}
        }
        
        for image_id, receipt_id in receipts:
            try:
                receipt_data = self.data_loader.load_receipt_by_id(image_id, receipt_id)
                if not receipt_data:
                    continue
                
                receipt, words, lines = receipt_data
                
                # Test unified pattern engine with different merchants
                test_merchants = ["McDonald's", "Walmart", "Target", None]
                
                for merchant in test_merchants:
                    results = await UNIFIED_PATTERN_ENGINE.detect_all_patterns(words, merchant)
                    
                    # Analyze results
                    for category, matches in results.items():
                        if category.startswith("_"):  # Skip metadata
                            continue
                        
                        for match in matches:
                            if hasattr(match, 'source_engine'):
                                if match.source_engine == "trie":
                                    feature_analysis["trie_based_detection"]["total_matches"] += 1
                                    if match.is_multi_word:
                                        feature_analysis["trie_based_detection"]["multi_word_matches"] += 1
                                
                                elif match.source_engine == "merchant":
                                    feature_analysis["merchant_patterns"]["total_matches"] += 1
                                    if match.is_merchant_specific:
                                        feature_analysis["merchant_patterns"]["merchant_specific"] += 1
                                
                                elif match.source_engine == "automaton":
                                    feature_analysis["automaton_keywords"]["total_matches"] += 1
                                    if match.is_multi_word:
                                        feature_analysis["automaton_keywords"]["phrase_matches"] += 1
                
            except Exception as e:
                logging.error(f"Error in feature analysis for {image_id}/{receipt_id}: {e}")
        
        return feature_analysis
    
    async def _run_pattern_coverage_analysis(self, receipts: List[tuple]) -> Dict[str, Any]:
        """Analyze pattern coverage across the dataset."""
        logging.info("Analyzing pattern coverage")
        
        pattern_stats = defaultdict(int)
        confidence_stats = defaultdict(list)
        category_stats = defaultdict(int)
        
        coverage_analysis = {
            "total_receipts_analyzed": 0,
            "pattern_distribution": {},
            "confidence_analysis": {},
            "category_coverage": {},
            "coverage_summary": {}
        }
        
        for image_id, receipt_id in receipts:
            try:
                receipt_data = self.data_loader.load_receipt_by_id(image_id, receipt_id)
                if not receipt_data:
                    continue
                
                receipt, words, lines = receipt_data
                
                # Run advanced pattern detection
                results = await detect_patterns_optimized(words)
                pattern_results = results.get("pattern_results", {})
                
                coverage_analysis["total_receipts_analyzed"] += 1
                
                # Analyze patterns found
                for category, matches in pattern_results.items():
                    if category.startswith("_"):  # Skip metadata
                        continue
                    
                    category_stats[category] += len(matches)
                    
                    for match in matches:
                        if hasattr(match, 'pattern_type'):
                            pattern_stats[match.pattern_type.value] += 1
                        if hasattr(match, 'confidence'):
                            confidence_stats[category].append(match.confidence)
                
            except Exception as e:
                logging.error(f"Error in coverage analysis for {image_id}/{receipt_id}: {e}")
        
        # Calculate statistics
        coverage_analysis["pattern_distribution"] = dict(pattern_stats)
        coverage_analysis["category_coverage"] = dict(category_stats)
        
        # Calculate confidence statistics
        for category, confidences in confidence_stats.items():
            if confidences:
                coverage_analysis["confidence_analysis"][category] = {
                    "average": sum(confidences) / len(confidences),
                    "min": min(confidences),
                    "max": max(confidences),
                    "count": len(confidences)
                }
        
        # Coverage summary
        total_patterns = sum(pattern_stats.values())
        coverage_analysis["coverage_summary"] = {
            "total_patterns_detected": total_patterns,
            "average_patterns_per_receipt": total_patterns / max(coverage_analysis["total_receipts_analyzed"], 1),
            "unique_pattern_types": len(pattern_stats),
            "coverage_categories": len(category_stats)
        }
        
        return coverage_analysis
    
    async def _run_merchant_analysis(self, receipts: List[tuple]) -> Dict[str, Any]:
        """Analyze merchant-specific pattern detection."""
        logging.info("Analyzing merchant-specific patterns")
        
        merchant_analysis = {
            "merchants_tested": ["McDonald's", "Walmart", "Target"],
            "merchant_results": {},
            "merchant_comparison": {}
        }
        
        test_receipts = receipts[:min(10, len(receipts))]
        
        for merchant in merchant_analysis["merchants_tested"]:
            merchant_results = {
                "total_matches": 0,
                "merchant_specific_matches": 0,
                "product_matches": 0,
                "transaction_pattern_matches": 0,
                "receipts_processed": 0
            }
            
            for image_id, receipt_id in test_receipts:
                try:
                    receipt_data = self.data_loader.load_receipt_by_id(image_id, receipt_id)
                    if not receipt_data:
                        continue
                    
                    receipt, words, lines = receipt_data
                    
                    # Test with merchant-specific patterns
                    results = await UNIFIED_PATTERN_ENGINE.detect_all_patterns(words, merchant)
                    
                    merchant_results["receipts_processed"] += 1
                    
                    for category, matches in results.items():
                        if category.startswith("_"):
                            continue
                        
                        for match in matches:
                            if hasattr(match, 'is_merchant_specific') and match.is_merchant_specific:
                                merchant_results["total_matches"] += 1
                                merchant_results["merchant_specific_matches"] += 1
                                
                                # Check if it's a product match
                                if hasattr(match, 'pattern_type') and 'PRODUCT' in match.pattern_type.value:
                                    merchant_results["product_matches"] += 1
                                
                                # Check metadata for transaction patterns
                                if hasattr(match, 'metadata') and match.metadata.get("pattern_source") == "transaction_patterns":
                                    merchant_results["transaction_pattern_matches"] += 1
                
                except Exception as e:
                    logging.error(f"Error in merchant analysis for {merchant} {image_id}/{receipt_id}: {e}")
            
            merchant_analysis["merchant_results"][merchant] = merchant_results
        
        # Calculate comparison statistics
        total_merchant_matches = sum(
            result["merchant_specific_matches"] 
            for result in merchant_analysis["merchant_results"].values()
        )
        
        merchant_analysis["merchant_comparison"] = {
            "total_merchant_specific_matches": total_merchant_matches,
            "merchants_with_matches": len([
                merchant for merchant, result in merchant_analysis["merchant_results"].items()
                if result["merchant_specific_matches"] > 0
            ])
        }
        
        return merchant_analysis
    
    def _save_results(self, results: Dict[str, Any]):
        """Save test results to files."""
        # Save comprehensive results
        results_file = self.output_dir / "pattern_detection_test_results.json"
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, default=str)
        
        # Save performance summary
        if "performance_comparison" in results and "summary" in results["performance_comparison"]:
            summary = results["performance_comparison"]["summary"]
            summary_file = self.output_dir / "performance_summary.json"
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, default=str)
        
        # Generate readable report
        self._generate_readable_report(results)
    
    def _generate_readable_report(self, results: Dict[str, Any]):
        """Generate a human-readable test report."""
        report_file = self.output_dir / "pattern_detection_report.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# Pattern Detection Enhancement Test Report\n\n")
            
            # Metadata
            metadata = results.get("metadata", {})
            f.write(f"**Test Date:** {metadata.get('test_started', 'Unknown')}\n")
            f.write(f"**Receipts Tested:** {metadata.get('total_receipts', 'Unknown')}\n")
            f.write(f"**Data Directory:** {metadata.get('data_directory', 'Unknown')}\n\n")
            
            # Performance Summary
            if "performance_comparison" in results:
                perf = results["performance_comparison"]
                f.write("## Performance Comparison\n\n")
                
                if "summary" in perf and "error" not in perf["summary"]:
                    summary = perf["summary"]
                    f.write(f"**Time Improvement:** {summary.get('time_improvement_percent', 0):.1f}%\n")
                    f.write(f"**Speedup Factor:** {summary.get('speedup_factor', 1.0):.2f}x\n")
                    f.write(f"**Legacy Average:** {summary.get('legacy_avg_ms', 0):.2f}ms\n")
                    f.write(f"**Advanced Average:** {summary.get('advanced_avg_ms', 0):.2f}ms\n\n")
                
                f.write("### Optimization Levels Tested\n\n")
                for level, data in perf.get("optimization_levels", {}).items():
                    f.write(f"- **{level.title()}:** {data.get('receipts_processed', 0)} receipts, ")
                    f.write(f"{data.get('average_processing_time_ms', 0):.2f}ms avg, ")
                    f.write(f"{data.get('total_patterns_detected', 0)} patterns\n")
                f.write("\n")
            
            # Pattern Coverage
            if "pattern_coverage" in results:
                coverage = results["pattern_coverage"]
                f.write("## Pattern Coverage Analysis\n\n")
                
                summary = coverage.get("coverage_summary", {})
                f.write(f"**Total Patterns Detected:** {summary.get('total_patterns_detected', 0)}\n")
                f.write(f"**Average per Receipt:** {summary.get('average_patterns_per_receipt', 0):.1f}\n")
                f.write(f"**Unique Pattern Types:** {summary.get('unique_pattern_types', 0)}\n\n")
                
                # Top patterns
                pattern_dist = coverage.get("pattern_distribution", {})
                if pattern_dist:
                    f.write("### Most Common Patterns\n\n")
                    sorted_patterns = sorted(pattern_dist.items(), key=lambda x: x[1], reverse=True)[:10]
                    for pattern, count in sorted_patterns:
                        f.write(f"- {pattern}: {count}\n")
                    f.write("\n")
            
            # Merchant Analysis
            if "merchant_analysis" in results:
                merchant = results["merchant_analysis"]
                f.write("## Merchant-Specific Pattern Analysis\n\n")
                
                for merchant_name, data in merchant.get("merchant_results", {}).items():
                    f.write(f"### {merchant_name}\n")
                    f.write(f"- **Total Matches:** {data.get('total_matches', 0)}\n")
                    f.write(f"- **Merchant-Specific:** {data.get('merchant_specific_matches', 0)}\n")
                    f.write(f"- **Product Matches:** {data.get('product_matches', 0)}\n")
                    f.write(f"- **Transaction Patterns:** {data.get('transaction_pattern_matches', 0)}\n\n")
            
            # Enhancement Features
            if "optimization_analysis" in results and "feature_analysis" in results["optimization_analysis"]:
                features = results["optimization_analysis"]["feature_analysis"]
                f.write("## Enhancement Features Analysis\n\n")
                
                for feature_name, data in features.items():
                    f.write(f"### {feature_name.replace('_', ' ').title()}\n")
                    for metric, value in data.items():
                        f.write(f"- **{metric.replace('_', ' ').title()}:** {value}\n")
                    f.write("\n")
            
            f.write("---\n")
            f.write(f"*Report generated on {metadata.get('test_completed', 'Unknown')}*\n")


async def main():
    """Main entry point for the test script."""
    parser = argparse.ArgumentParser(description="Test pattern detection enhancements")
    parser.add_argument("--data-dir", default="./receipt_data", 
                       help="Directory containing local receipt data")
    parser.add_argument("--output-dir", default="./test_results", 
                       help="Directory to save test results")
    parser.add_argument("--max-receipts", type=int, 
                       help="Maximum number of receipts to test")
    parser.add_argument("--verbose", "-v", action="store_true", 
                       help="Verbose logging")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    try:
        # Create tester
        tester = PatternDetectionTester(
            data_dir=args.data_dir,
            output_dir=args.output_dir
        )
        
        # Run comprehensive test
        results = await tester.run_comprehensive_test(max_receipts=args.max_receipts)
        
        # Print summary
        print("\n" + "="*60)
        print("PATTERN DETECTION TEST SUMMARY")
        print("="*60)
        
        metadata = results.get("metadata", {})
        print(f"Receipts Tested: {metadata.get('total_receipts', 'Unknown')}")
        
        if "performance_comparison" in results and "summary" in results["performance_comparison"]:
            summary = results["performance_comparison"]["summary"]
            if "error" not in summary:
                print(f"Performance Improvement: {summary.get('time_improvement_percent', 0):.1f}%")
                print(f"Speedup Factor: {summary.get('speedup_factor', 1.0):.2f}x")
        
        if "pattern_coverage" in results:
            coverage_summary = results["pattern_coverage"].get("coverage_summary", {})
            print(f"Total Patterns Detected: {coverage_summary.get('total_patterns_detected', 0)}")
            print(f"Average Patterns per Receipt: {coverage_summary.get('average_patterns_per_receipt', 0):.1f}")
        
        print(f"\nDetailed results saved to: {args.output_dir}")
        print(f"Report available at: {args.output_dir}/pattern_detection_report.md")
        
    except KeyboardInterrupt:
        print("\nTest cancelled by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Test failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())