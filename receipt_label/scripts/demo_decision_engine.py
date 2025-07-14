#!/usr/bin/env python3
"""Demo Smart Decision Engine with synthetic data.

This script demonstrates the Phase 1 Smart Decision Engine implementation
using synthetic receipt data to verify end-to-end functionality.
"""

import asyncio
import logging

# Add the project root to the path
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.entities.receipt_word import ReceiptWord

from receipt_label.decision_engine import (
    ConfidenceLevel,
    DecisionEngineConfig,
    DecisionOutcome,
    create_aggressive_config,
    create_conservative_config,
    process_receipt_with_decision_engine,
)


def create_word(
    text: str,
    x: int,
    y: int,
    width: int = 50,
    height: int = 15,
    word_id: int = 1,
    line_id: int = 1,
) -> ReceiptWord:
    """Helper to create ReceiptWord with proper geometry."""
    return ReceiptWord(
        receipt_id=1,
        image_id="test-image",
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": width, "height": height},
        top_left={"x": x, "y": y},
        top_right={"x": x + width, "y": y},
        bottom_left={"x": x, "y": y + height},
        bottom_right={"x": x + width, "y": y + height},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


@dataclass
class SyntheticReceipt:
    """Synthetic receipt for testing."""

    name: str
    merchant: str
    words: List[ReceiptWord]
    expected_decision: DecisionOutcome
    description: str


def create_synthetic_receipts() -> List[SyntheticReceipt]:
    """Create a variety of synthetic receipts for testing."""

    receipts = []

    # 1. Perfect receipt - should SKIP
    words = [
        create_word("WALMART", 100, 50, 80, 20, 1, 1),
        create_word("SUPERCENTER", 180, 50, 100, 20, 2, 1),
        create_word("Store", 100, 70, 40, 15, 3, 2),
        create_word("#1234", 145, 70, 50, 15, 4, 2),
        create_word("01/15/2024", 100, 100, 80, 15, 5, 3),
        create_word("Bananas", 50, 150, 60, 15, 6, 4),
        create_word("2.5", 120, 150, 30, 15, 7, 4),
        create_word("@", 155, 150, 15, 15, 8, 4),
        create_word("$0.68", 175, 150, 40, 15, 9, 4),
        create_word("$1.70", 220, 150, 40, 15, 10, 4),
        create_word("Milk", 50, 170, 40, 15, 11, 5),
        create_word("1", 120, 170, 15, 15, 12, 5),
        create_word("@", 140, 170, 15, 15, 13, 5),
        create_word("$3.49", 160, 170, 40, 15, 14, 5),
        create_word("$3.49", 220, 170, 40, 15, 15, 5),
        create_word("SUBTOTAL", 150, 200, 70, 15, 16, 6),
        create_word("$5.19", 220, 200, 40, 15, 17, 6),
        create_word("TAX", 180, 220, 30, 15, 18, 7),
        create_word("$0.31", 220, 220, 40, 15, 19, 7),
        create_word("TOTAL", 170, 240, 40, 15, 20, 8),
        create_word("$5.50", 220, 240, 40, 15, 21, 8),
    ]

    receipts.append(
        SyntheticReceipt(
            name="perfect_walmart",
            merchant="Walmart",
            words=words,
            expected_decision=DecisionOutcome.SKIP,
            description="Complete receipt with all essential fields and good coverage",
        )
    )

    # 2. Missing merchant - should REQUIRE
    receipts.append(
        SyntheticReceipt(
            name="missing_merchant",
            merchant="Unknown",
            words=[
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="1",
                    text="Store",
                    x=100,
                    y=50,
                    width=40,
                    height=20,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="2",
                    text="#5678",
                    x=145,
                    y=50,
                    width=50,
                    height=20,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="3",
                    text="01/15/2024",
                    x=100,
                    y=100,
                    width=80,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="4",
                    text="Item",
                    x=50,
                    y=150,
                    width=40,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="5",
                    text="$12.99",
                    x=200,
                    y=150,
                    width=50,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="6",
                    text="TOTAL",
                    x=170,
                    y=200,
                    width=40,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="7",
                    text="$12.99",
                    x=220,
                    y=200,
                    width=50,
                    height=15,
                ),
            ],
            expected_decision=DecisionOutcome.REQUIRED,
            description="Missing merchant name - critical field",
        )
    )

    # 3. Missing product but good coverage - should BATCH
    receipts.append(
        SyntheticReceipt(
            name="missing_product",
            merchant="Target",
            words=[
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="1",
                    text="TARGET",
                    x=100,
                    y=50,
                    width=60,
                    height=20,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="2",
                    text="Store",
                    x=170,
                    y=50,
                    width=40,
                    height=20,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="3",
                    text="T-1234",
                    x=215,
                    y=50,
                    width=50,
                    height=20,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="4",
                    text="01/15/2024",
                    x=100,
                    y=100,
                    width=80,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="5",
                    text="Item",
                    x=50,
                    y=150,
                    width=40,
                    height=15,
                ),  # Generic item name
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="6",
                    text="Code",
                    x=100,
                    y=150,
                    width=40,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="7",
                    text="123456",
                    x=150,
                    y=150,
                    width=50,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="8",
                    text="$15.99",
                    x=220,
                    y=150,
                    width=50,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="9",
                    text="SUBTOTAL",
                    x=150,
                    y=180,
                    width=70,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="10",
                    text="$15.99",
                    x=220,
                    y=180,
                    width=50,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="11",
                    text="TOTAL",
                    x=170,
                    y=200,
                    width=40,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="12",
                    text="$15.99",
                    x=220,
                    y=200,
                    width=50,
                    height=15,
                ),
            ],
            expected_decision=DecisionOutcome.BATCH,
            description="Good coverage but no clear product names",
        )
    )

    # 4. Low coverage - should REQUIRE
    receipts.append(
        SyntheticReceipt(
            name="low_coverage",
            merchant="McDonald's",
            words=[
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="1",
                    text="McDonald's",
                    x=100,
                    y=50,
                    width=80,
                    height=20,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="2",
                    text="###",
                    x=50,
                    y=80,
                    width=30,
                    height=15,
                ),  # Noise
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="3",
                    text="ILLEGIBLE",
                    x=90,
                    y=80,
                    width=70,
                    height=15,
                ),  # Can't read
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="4",
                    text="FUZZY_TEXT",
                    x=170,
                    y=80,
                    width=80,
                    height=15,
                ),  # Poor OCR
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="5",
                    text="01/15/2024",
                    x=100,
                    y=100,
                    width=80,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="6",
                    text="???",
                    x=50,
                    y=130,
                    width=30,
                    height=15,
                ),  # Noise
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="7",
                    text="UNCLEAR",
                    x=90,
                    y=130,
                    width=60,
                    height=15,
                ),  # Can't read
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="8",
                    text="CORRUPTED",
                    x=160,
                    y=130,
                    width=80,
                    height=15,
                ),  # Poor OCR
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="9",
                    text="BIG",
                    x=50,
                    y=150,
                    width=30,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="10",
                    text="MAC",
                    x=90,
                    y=150,
                    width=30,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="11",
                    text="$6.99",
                    x=220,
                    y=150,
                    width=50,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="12",
                    text="TOTAL",
                    x=170,
                    y=200,
                    width=40,
                    height=15,
                ),
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id="13",
                    text="$6.99",
                    x=220,
                    y=200,
                    width=50,
                    height=15,
                ),
            ],
            expected_decision=DecisionOutcome.REQUIRED,
            description="Low coverage due to poor OCR quality",
        )
    )

    # 5. High volume receipt - should test performance
    receipts.append(
        SyntheticReceipt(
            name="high_volume",
            merchant="Costco",
            words=[
                ReceiptWord(
                    receipt_id="test",
                    image_id="test",
                    word_id=str(i),
                    text=word,
                    x=50 + (i % 10) * 20,
                    y=50 + (i // 10) * 20,
                    width=18,
                    height=15,
                )
                for i, word in enumerate(
                    [
                        "COSTCO",
                        "WHOLESALE",
                        "CORP",
                        "#123",
                        "01/15/2024",
                        "Member",
                        "#123456789",
                        "Organic",
                        "Bananas",
                        "3",
                        "lbs",
                        "@",
                        "$0.69",
                        "$2.07",
                        "Kirkland",
                        "Signature",
                        "Olive",
                        "Oil",
                        "1",
                        "@",
                        "$12.99",
                        "$12.99",
                        "Rotisserie",
                        "Chicken",
                        "1",
                        "@",
                        "$4.99",
                        "$4.99",
                        "Paper",
                        "Towels",
                        "12",
                        "pack",
                        "1",
                        "@",
                        "$18.99",
                        "$18.99",
                        "Laundry",
                        "Detergent",
                        "2",
                        "@",
                        "$13.99",
                        "$27.98",
                        "Subtotal",
                        "$66.03",
                        "Tax",
                        "$4.62",
                        "Total",
                        "$70.65",
                        "Visa",
                        "****1234",
                        "Amount",
                        "$70.65",
                        "Thank",
                        "You",
                    ]
                )
            ],
            expected_decision=DecisionOutcome.SKIP,
            description="High volume receipt with many products",
        )
    )

    return receipts


async def test_single_receipt(
    receipt: SyntheticReceipt, config: DecisionEngineConfig
) -> dict:
    """Test decision engine on a single synthetic receipt."""

    print(f"\n{'='*60}")
    print(f"Testing: {receipt.name} ({receipt.merchant})")
    print(f"Description: {receipt.description}")
    print(f"Expected: {receipt.expected_decision.value.upper()}")
    print(f"{'='*60}")

    try:
        # Process with decision engine
        result = await process_receipt_with_decision_engine(
            words=receipt.words,
            config=config,
            client_manager=None,  # No Pinecone in demo
            receipt_context={"receipt_name": receipt.name},
        )

        decision = result.decision

        # Display results
        print(f"🎯 DECISION: {decision.action.value.upper()}")
        print(f"📊 CONFIDENCE: {decision.confidence.value}")
        print(f"💭 REASONING: {decision.reasoning}")
        print(f"📈 COVERAGE: {decision.coverage_percentage:.1f}%")
        print(
            f"📝 LABELED WORDS: {decision.labeled_words}/{decision.total_words}"
        )
        print(f"❓ UNLABELED: {decision.unlabeled_meaningful_words}")
        print(f"⏱️  DECISION TIME: {result.decision_time_ms:.1f}ms")

        # Essential fields
        print(f"\n🔍 ESSENTIAL FIELDS:")
        if decision.essential_fields_found:
            print(f"   ✅ Found: {', '.join(decision.essential_fields_found)}")
        if decision.essential_fields_missing:
            print(
                f"   ❌ Missing: {', '.join(decision.essential_fields_missing)}"
            )

        # Check if decision matches expectation
        if decision.action == receipt.expected_decision:
            print(f"\n✅ PASS: Decision matches expectation")
            status = "PASS"
        else:
            print(
                f"\n❌ FAIL: Expected {receipt.expected_decision.value}, got {decision.action.value}"
            )
            status = "FAIL"

        # Check performance
        if result.decision_time_ms and result.decision_time_ms > 10.0:
            print(
                f"⚠️  WARNING: Decision time ({result.decision_time_ms:.1f}ms) exceeds 10ms target"
            )

        return {
            "name": receipt.name,
            "expected": receipt.expected_decision.value,
            "actual": decision.action.value,
            "status": status,
            "coverage": decision.coverage_percentage,
            "decision_time_ms": result.decision_time_ms or 0.0,
            "confidence": decision.confidence.value,
            "reasoning": decision.reasoning,
        }

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return {
            "name": receipt.name,
            "expected": receipt.expected_decision.value,
            "actual": "ERROR",
            "status": "ERROR",
            "coverage": 0.0,
            "decision_time_ms": 0.0,
            "confidence": "unknown",
            "reasoning": str(e),
        }


async def run_demo(config_name: str = "default"):
    """Run the complete Smart Decision Engine demo."""

    print("🚀 SMART DECISION ENGINE DEMO")
    print("=" * 60)

    # Create configuration
    if config_name == "conservative":
        config = create_conservative_config()
        print("📋 Using CONSERVATIVE configuration (strict thresholds)")
    elif config_name == "aggressive":
        config = create_aggressive_config()
        print("📋 Using AGGRESSIVE configuration (lenient thresholds)")
    else:
        config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)
        print("📋 Using DEFAULT configuration")

    print(f"   • Min Coverage: {config.min_coverage_percentage}%")
    print(f"   • Max Unlabeled Words: {config.max_unlabeled_words}")
    print(f"   • Min Confidence: {config.min_pattern_confidence}")

    # Create synthetic receipts
    receipts = create_synthetic_receipts()
    print(f"\n🧪 Testing with {len(receipts)} synthetic receipts")

    # Test each receipt
    results = []
    for receipt in receipts:
        result = await test_single_receipt(receipt, config)
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print("📊 DEMO SUMMARY")
    print(f"{'='*60}")

    total_tests = len(results)
    passes = len([r for r in results if r["status"] == "PASS"])
    errors = len([r for r in results if r["status"] == "ERROR"])

    print(f"Total tests: {total_tests}")
    print(f"Passes: {passes}")
    print(f"Fails: {total_tests - passes - errors}")
    print(f"Errors: {errors}")

    # Decision distribution
    skip_count = len([r for r in results if r["actual"] == "skip"])
    batch_count = len([r for r in results if r["actual"] == "batch"])
    required_count = len([r for r in results if r["actual"] == "required"])

    print(f"\n🎯 DECISION DISTRIBUTION:")
    print(f"   SKIP: {skip_count} ({skip_count/total_tests*100:.1f}%)")
    print(f"   BATCH: {batch_count} ({batch_count/total_tests*100:.1f}%)")
    print(
        f"   REQUIRED: {required_count} ({required_count/total_tests*100:.1f}%)"
    )

    # Performance
    valid_times = [
        r["decision_time_ms"] for r in results if r["decision_time_ms"] > 0
    ]
    if valid_times:
        avg_time = sum(valid_times) / len(valid_times)
        max_time = max(valid_times)
        print(f"\n⏱️  PERFORMANCE:")
        print(f"   Average decision time: {avg_time:.1f}ms")
        print(f"   Max decision time: {max_time:.1f}ms")
        print(
            f"   Performance target (<10ms): {'✅ PASS' if avg_time < 10.0 else '❌ FAIL'}"
        )

    # Overall assessment
    print(f"\n🏆 OVERALL ASSESSMENT:")
    if passes == total_tests and errors == 0:
        print("   🎉 ALL TESTS PASSED - Decision engine working correctly!")
    elif passes >= total_tests * 0.8:
        print("   ⚠️  MOSTLY SUCCESSFUL - Minor issues detected")
    else:
        print("   ❌ SIGNIFICANT ISSUES - Configuration may need adjustment")

    print(f"\n💡 Next steps:")
    print(
        f"   1. Test with real receipt data using test_decision_engine_local.py"
    )
    print(f"   2. Tune configuration based on actual performance")
    print(f"   3. Enable Pinecone integration for merchant reliability")
    print(f"   4. Deploy with feature flags for gradual rollout")


async def main():
    """Main demo function."""
    import argparse

    parser = argparse.ArgumentParser(description="Demo Smart Decision Engine")
    parser.add_argument(
        "--config",
        choices=["default", "conservative", "aggressive"],
        default="default",
        help="Configuration preset to use",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)  # Quiet for demo

    await run_demo(args.config)


if __name__ == "__main__":
    asyncio.run(main())
