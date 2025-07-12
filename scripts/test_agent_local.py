#!/usr/bin/env python3
"""
Local testing script for the agent-based receipt labeling system.

This script enables comprehensive testing without AWS dependencies or API costs.
It uses mock services and provides performance profiling capabilities.
"""

import argparse
import asyncio
import json
import logging

# Add parent directory to path
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from mock_services import (
    MockClientManager,
    MockDynamoClient,
    create_sample_receipt,
)
from receipt_label.agent.receipt_labeler_agent import ReceiptLabelerAgent
from receipt_label.utils.client_manager import ClientConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class LocalTestRunner:
    """Runs local tests for the agent labeling system."""

    def __init__(
        self, mock_gpt: bool = True, profile_performance: bool = False
    ):
        """Initialize test runner."""
        self.mock_gpt = mock_gpt
        self.profile_performance = profile_performance
        self.client_manager = MockClientManager()
        self.agent = ReceiptLabelerAgent(self.client_manager)

        # Track test metrics
        self.test_results = {
            "total_tests": 0,
            "passed": 0,
            "failed": 0,
            "performance_metrics": [],
            "gpt_call_reduction": [],
        }

    async def test_pattern_only_labeling(self) -> Dict:
        """Test labeling with patterns only (no GPT needed)."""
        logger.info("Testing pattern-only labeling...")

        # Create a simple receipt with clear patterns
        words, lines, metadata = create_sample_receipt("test_001", "WALMART")

        # Add to mock DynamoDB
        self.client_manager.dynamo.add_test_receipt(
            "test_001", words, lines, metadata
        )

        # Run labeling
        start_time = time.time()
        result = await self.agent.label_receipt(
            receipt_id="test_001",
            receipt_words=words,
            receipt_lines=lines,
            receipt_metadata=metadata.to_dict() if metadata else None,
            store_labels=False,  # Don't store in mock DB
        )
        elapsed_time = (time.time() - start_time) * 1000  # Convert to ms

        # Verify results
        test_passed = True
        errors = []

        # Check pattern detection performance
        if elapsed_time > 200:  # Should be under 200ms
            errors.append(
                f"Pattern detection took {elapsed_time:.0f}ms (target: <200ms)"
            )
            test_passed = False

        # Check if GPT was called
        if result.get("gpt_called", False):
            errors.append(
                "GPT was called when patterns should have been sufficient"
            )
            test_passed = False

        # Check essential labels found
        labeled_words = result.get("labeled_words", {})
        essential_found = {
            "MERCHANT_NAME": any(
                lw.get("label") == "MERCHANT_NAME"
                for lw in labeled_words.values()
            ),
            "DATE": any(
                lw.get("label") == "DATE" for lw in labeled_words.values()
            ),
            "GRAND_TOTAL": any(
                lw.get("label") == "GRAND_TOTAL"
                for lw in labeled_words.values()
            ),
        }

        for label, found in essential_found.items():
            if not found:
                errors.append(f"Essential label {label} not found")
                test_passed = False

        # Record results
        self.test_results["total_tests"] += 1
        if test_passed:
            self.test_results["passed"] += 1
        else:
            self.test_results["failed"] += 1

        self.test_results["performance_metrics"].append(
            {
                "test": "pattern_only_labeling",
                "elapsed_ms": elapsed_time,
                "word_count": len(words),
            }
        )

        return {
            "test_name": "Pattern-Only Labeling",
            "passed": test_passed,
            "elapsed_ms": elapsed_time,
            "errors": errors,
            "labeled_count": len(labeled_words),
            "gpt_called": result.get("gpt_called", False),
        }

    async def test_gpt_decision_logic(self) -> Dict:
        """Test smart GPT decision making."""
        logger.info("Testing GPT decision logic...")

        # Create receipt missing essential labels
        words = [
            {
                "word_id": 1,
                "text": "STORE",
                "line_id": 1,
                "bounding_box": {
                    "x": 100,
                    "y": 50,
                    "width": 100,
                    "height": 30,
                },
            },
            {
                "word_id": 2,
                "text": "RECEIPT",
                "line_id": 1,
                "bounding_box": {
                    "x": 210,
                    "y": 50,
                    "width": 100,
                    "height": 30,
                },
            },
            # Missing date, total, product - should trigger GPT
            {
                "word_id": 3,
                "text": "THANK",
                "line_id": 2,
                "bounding_box": {
                    "x": 100,
                    "y": 100,
                    "width": 80,
                    "height": 25,
                },
            },
            {
                "word_id": 4,
                "text": "YOU",
                "line_id": 2,
                "bounding_box": {
                    "x": 190,
                    "y": 100,
                    "width": 50,
                    "height": 25,
                },
            },
        ]

        lines = [
            {"line_id": 1, "text": "STORE RECEIPT", "words": [1, 2]},
            {"line_id": 2, "text": "THANK YOU", "words": [3, 4]},
        ]

        # Convert to entities for testing
        from receipt_dynamo.entities import ReceiptLine, ReceiptWord

        word_entities = []
        for w in words:
            word_entities.append(
                ReceiptWord(
                    receipt_id=2,
                    image_id="img_test_002",
                    line_id=w["line_id"],
                    word_id=w["word_id"],
                    text=w["text"],
                    bounding_box=w["bounding_box"],
                    top_left={
                        "x": w["bounding_box"]["x"],
                        "y": w["bounding_box"]["y"],
                    },
                    top_right={
                        "x": w["bounding_box"]["x"]
                        + w["bounding_box"]["width"],
                        "y": w["bounding_box"]["y"],
                    },
                    bottom_left={
                        "x": w["bounding_box"]["x"],
                        "y": w["bounding_box"]["y"]
                        + w["bounding_box"]["height"],
                    },
                    bottom_right={
                        "x": w["bounding_box"]["x"]
                        + w["bounding_box"]["width"],
                        "y": w["bounding_box"]["y"]
                        + w["bounding_box"]["height"],
                    },
                    angle_degrees=0.0,
                    angle_radians=0.0,
                    confidence=0.95,
                )
            )

        line_entities = []
        for l in lines:
            line_entities.append(
                ReceiptLine(
                    receipt_id=2,
                    image_id="img_test_002",
                    line_id=l["line_id"],
                    text=l["text"],
                    words=l["words"],
                    bounding_box={
                        "x": 100,
                        "y": 50 * l["line_id"],
                        "width": 300,
                        "height": 30,
                    },
                )
            )

        # Run labeling
        result = await self.agent.label_receipt(
            receipt_id="test_002",
            receipt_words=word_entities,
            receipt_lines=line_entities,
            store_labels=False,
        )

        # Verify GPT was called due to missing essential labels
        test_passed = result.get("gpt_called", False)
        errors = []

        if not test_passed:
            errors.append(
                "GPT should have been called due to missing essential labels"
            )

        # Check decision reason
        decision_reason = result.get("decision_reason", "")
        if "Missing essential labels" not in decision_reason:
            errors.append(f"Unexpected decision reason: {decision_reason}")
            test_passed = False

        self.test_results["total_tests"] += 1
        if test_passed:
            self.test_results["passed"] += 1
        else:
            self.test_results["failed"] += 1

        return {
            "test_name": "GPT Decision Logic",
            "passed": test_passed,
            "errors": errors,
            "gpt_called": result.get("gpt_called", False),
            "decision_reason": decision_reason,
        }

    async def test_batch_processing(self) -> Dict:
        """Test batch processing logic."""
        logger.info("Testing batch processing...")

        # Create receipt with many unlabeled words
        words = []
        for i in range(20):  # 20 words, many will be unlabeled
            words.append(
                {
                    "word_id": i + 1,
                    "text": f"WORD{i}",
                    "line_id": i // 4 + 1,
                    "bounding_box": {
                        "x": 100 + (i % 4) * 80,
                        "y": 50 + (i // 4) * 30,
                        "width": 70,
                        "height": 25,
                    },
                }
            )

        # Add some patterns
        words[0]["text"] = "WALMART"  # Merchant
        words[5]["text"] = "01/15/2024"  # Date
        words[15]["text"] = "$99.99"  # Total (will be detected as currency)
        words[10]["text"] = "PRODUCT"  # Product

        # Convert to entities
        from receipt_dynamo.entities import ReceiptLine, ReceiptWord

        word_entities = []
        for w in words:
            word_entities.append(
                ReceiptWord(
                    receipt_id=3,
                    image_id="img_test_003",
                    line_id=w["line_id"],
                    word_id=w["word_id"],
                    text=w["text"],
                    bounding_box=w["bounding_box"],
                    top_left={
                        "x": w["bounding_box"]["x"],
                        "y": w["bounding_box"]["y"],
                    },
                    top_right={
                        "x": w["bounding_box"]["x"]
                        + w["bounding_box"]["width"],
                        "y": w["bounding_box"]["y"],
                    },
                    bottom_left={
                        "x": w["bounding_box"]["x"],
                        "y": w["bounding_box"]["y"]
                        + w["bounding_box"]["height"],
                    },
                    bottom_right={
                        "x": w["bounding_box"]["x"]
                        + w["bounding_box"]["width"],
                        "y": w["bounding_box"]["y"]
                        + w["bounding_box"]["height"],
                    },
                    angle_degrees=0.0,
                    angle_radians=0.0,
                    confidence=0.95,
                )
            )

        line_entities = []
        for i in range(5):
            line_entities.append(
                ReceiptLine(
                    receipt_id=3,
                    image_id="img_test_003",
                    line_id=i + 1,
                    text=" ".join(
                        [w["text"] for w in words if w["line_id"] == i + 1]
                    ),
                    words=[
                        w["word_id"] for w in words if w["line_id"] == i + 1
                    ],
                    bounding_box={
                        "x": 100,
                        "y": 50 + i * 30,
                        "width": 400,
                        "height": 25,
                    },
                )
            )

        # Run labeling
        result = await self.agent.label_receipt(
            receipt_id="test_003",
            receipt_words=word_entities,
            receipt_lines=line_entities,
            store_labels=False,
        )

        # Check if batch processing was triggered
        test_passed = True
        errors = []

        # Should have many unlabeled words (> threshold of 5)
        unlabeled_count = result.get("stats", {}).get("unlabeled_words", 0)
        if unlabeled_count <= 5:
            errors.append(
                f"Expected >5 unlabeled words, got {unlabeled_count}"
            )
            test_passed = False

        # GPT should be called due to threshold
        if not result.get("gpt_called", False):
            errors.append(
                "GPT should have been called due to unlabeled word threshold"
            )
            test_passed = False

        self.test_results["total_tests"] += 1
        if test_passed:
            self.test_results["passed"] += 1
        else:
            self.test_results["failed"] += 1

        return {
            "test_name": "Batch Processing",
            "passed": test_passed,
            "errors": errors,
            "unlabeled_count": unlabeled_count,
            "gpt_called": result.get("gpt_called", False),
        }

    async def test_performance_metrics(self) -> Dict:
        """Test performance across different receipt sizes."""
        logger.info("Testing performance metrics...")

        results = []

        # Test different receipt sizes
        for word_count in [10, 50, 100, 200]:
            # Create receipt with specified word count
            words = []
            for i in range(word_count):
                words.append(
                    {
                        "word_id": i + 1,
                        "text": f"WORD{i}",
                        "line_id": i // 10 + 1,
                        "bounding_box": {
                            "x": 100 + (i % 10) * 40,
                            "y": 50 + (i // 10) * 25,
                            "width": 35,
                            "height": 20,
                        },
                    }
                )

            # Add essential patterns
            if word_count > 0:
                words[0]["text"] = "MERCHANT"
            if word_count > 5:
                words[5]["text"] = "01/15/2024"
            if word_count > 10:
                words[10]["text"] = "$99.99"

            # Convert to entities
            from receipt_dynamo.entities import ReceiptLine, ReceiptWord

            word_entities = []
            for w in words:
                word_entities.append(
                    ReceiptWord(
                        receipt_id=100 + word_count,
                        image_id=f"img_perf_{word_count}",
                        line_id=w["line_id"],
                        word_id=w["word_id"],
                        text=w["text"],
                        bounding_box=w["bounding_box"],
                        top_left={
                            "x": w["bounding_box"]["x"],
                            "y": w["bounding_box"]["y"],
                        },
                        top_right={
                            "x": w["bounding_box"]["x"]
                            + w["bounding_box"]["width"],
                            "y": w["bounding_box"]["y"],
                        },
                        bottom_left={
                            "x": w["bounding_box"]["x"],
                            "y": w["bounding_box"]["y"]
                            + w["bounding_box"]["height"],
                        },
                        bottom_right={
                            "x": w["bounding_box"]["x"]
                            + w["bounding_box"]["width"],
                            "y": w["bounding_box"]["y"]
                            + w["bounding_box"]["height"],
                        },
                        angle_degrees=0.0,
                        angle_radians=0.0,
                        confidence=0.95,
                    )
                )

            # Measure pattern detection time only
            start_time = time.time()

            # Run pattern detection directly
            pattern_results = (
                await self.agent.pattern_detector.detect_all_patterns(
                    receipt_words=word_entities,
                    receipt_lines=[],  # Empty for performance test
                )
            )

            elapsed_time = (time.time() - start_time) * 1000  # ms

            results.append(
                {
                    "word_count": word_count,
                    "elapsed_ms": elapsed_time,
                    "patterns_found": sum(
                        len(patterns)
                        for name, patterns in pattern_results.items()
                        if name != "_metadata"
                    ),
                }
            )

            logger.info(f"  {word_count} words: {elapsed_time:.1f}ms")

        # Check if performance meets targets
        test_passed = True
        errors = []

        for result in results:
            # Target: <100ms for receipts up to 100 words
            if result["word_count"] <= 100 and result["elapsed_ms"] > 200:
                errors.append(
                    f"{result['word_count']} words took {result['elapsed_ms']:.1f}ms (target: <200ms)"
                )
                test_passed = False

        self.test_results["total_tests"] += 1
        if test_passed:
            self.test_results["passed"] += 1
        else:
            self.test_results["failed"] += 1

        self.test_results["performance_metrics"].extend(results)

        return {
            "test_name": "Performance Metrics",
            "passed": test_passed,
            "errors": errors,
            "results": results,
        }

    async def run_all_tests(self) -> None:
        """Run all tests and generate report."""
        logger.info("Starting local agent testing suite...")
        logger.info("=" * 60)

        test_results = []

        # Run each test
        tests = [
            self.test_pattern_only_labeling,
            self.test_gpt_decision_logic,
            self.test_batch_processing,
            self.test_performance_metrics,
        ]

        for test_func in tests:
            try:
                result = await test_func()
                test_results.append(result)

                # Print result
                status = "✅ PASSED" if result["passed"] else "❌ FAILED"
                logger.info(f"{status} - {result['test_name']}")

                if result.get("errors"):
                    for error in result["errors"]:
                        logger.error(f"  - {error}")

            except Exception as e:
                logger.error(f"Test {test_func.__name__} crashed: {e}")
                self.test_results["failed"] += 1
                test_results.append(
                    {
                        "test_name": test_func.__name__,
                        "passed": False,
                        "errors": [str(e)],
                    }
                )

        # Generate summary report
        logger.info("=" * 60)
        logger.info("Test Summary:")
        logger.info(f"  Total Tests: {self.test_results['total_tests']}")
        logger.info(f"  Passed: {self.test_results['passed']}")
        logger.info(f"  Failed: {self.test_results['failed']}")

        # Performance summary
        if self.test_results["performance_metrics"]:
            logger.info("\nPerformance Summary:")
            for metric in self.test_results["performance_metrics"]:
                if "word_count" in metric:
                    logger.info(
                        f"  {metric['word_count']} words: {metric['elapsed_ms']:.1f}ms"
                    )

        # Save detailed report
        report_path = Path("local_test_report.json")
        with open(report_path, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "summary": self.test_results,
                    "detailed_results": test_results,
                },
                f,
                indent=2,
            )
        logger.info(f"\nDetailed report saved to: {report_path}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Local testing for agent-based receipt labeling"
    )
    parser.add_argument(
        "--no-mock-gpt",
        action="store_true",
        help="Use real OpenAI API (costs money!)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable performance profiling",
    )

    args = parser.parse_args()

    # Run tests
    runner = LocalTestRunner(
        mock_gpt=not args.no_mock_gpt,
        profile_performance=args.profile,
    )

    await runner.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
