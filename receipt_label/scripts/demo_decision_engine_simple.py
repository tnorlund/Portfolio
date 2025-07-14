#!/usr/bin/env python3
"""Simple Smart Decision Engine demo with synthetic data.

This script demonstrates the Phase 1 Smart Decision Engine implementation
using minimal synthetic receipt data to verify functionality.
"""

import asyncio
import logging

# Add the project root to the path
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.entities.receipt_word import ReceiptWord

from receipt_label.decision_engine import (
    DecisionEngineConfig,
    DecisionOutcome,
    process_receipt_with_decision_engine,
)


def create_word(text: str, word_id: int) -> ReceiptWord:
    """Helper to create ReceiptWord with minimal required fields."""
    return ReceiptWord(
        receipt_id=1,
        image_id="550e8400-e29b-41d4-a716-446655440000",  # Valid UUID
        line_id=1,
        word_id=word_id,
        text=text,
        bounding_box={
            "x": 100,
            "y": 50 + word_id * 20,
            "width": 80,
            "height": 15,
        },
        top_left={"x": 100, "y": 50 + word_id * 20},
        top_right={"x": 180, "y": 50 + word_id * 20},
        bottom_left={"x": 100, "y": 65 + word_id * 20},
        bottom_right={"x": 180, "y": 65 + word_id * 20},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


async def test_simple_case():
    """Test a simple case with the decision engine."""

    print("🚀 SMART DECISION ENGINE SIMPLE DEMO")
    print("=" * 50)

    # Create a simple receipt with essential fields
    words = [
        create_word("WALMART", 1),
        create_word("01/15/2024", 2),
        create_word("Bananas", 3),
        create_word("$2.99", 4),
        create_word("TOTAL", 5),
        create_word("$2.99", 6),
    ]

    print(f"📝 Created receipt with {len(words)} words")
    for word in words:
        print(f"   • {word.text}")

    # Test with default configuration
    config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)

    print(f"\n🔧 Configuration:")
    print(f"   • Min Coverage: {config.min_coverage_percentage}%")
    print(f"   • Max Unlabeled Words: {config.max_unlabeled_words}")

    try:
        print(f"\n⚡ Processing with Decision Engine...")

        result = await process_receipt_with_decision_engine(
            words=words,
            config=config,
            client_manager=None,
            receipt_context={"test": "simple_case"},
        )

        decision = result.decision

        print(f"\n🎯 RESULTS:")
        print(f"   Decision: {decision.action.value.upper()}")
        print(f"   Confidence: {decision.confidence.value}")
        print(f"   Reasoning: {decision.reasoning}")
        print(f"   Coverage: {decision.coverage_percentage:.1f}%")
        print(f"   Processing Time: {result.processing_time_ms:.1f}ms")

        if decision.essential_fields_found:
            print(
                f"   ✅ Essential Fields Found: {', '.join(decision.essential_fields_found)}"
            )
        if decision.essential_fields_missing:
            print(
                f"   ❌ Essential Fields Missing: {', '.join(decision.essential_fields_missing)}"
            )

        # Determine success
        if decision.action == DecisionOutcome.SKIP:
            print(f"\n🎉 SUCCESS: Decision engine determined GPT not needed!")
            print(f"💰 This would save API costs for this receipt.")
        elif decision.action == DecisionOutcome.BATCH:
            print(f"\n⏳ BATCH: Decision engine recommends batch processing")
            print(f"💰 This would use cheaper batch API rates.")
        else:
            print(
                f"\n🔄 REQUIRED: Decision engine recommends immediate GPT processing"
            )
            print(f"📋 This ensures critical fields are not missed.")

        return True

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Main demo function."""

    # Quiet logging for demo
    logging.basicConfig(level=logging.WARNING)

    success = await test_simple_case()

    print(f"\n" + "=" * 50)
    if success:
        print("✅ Demo completed successfully!")
        print("\n💡 Next steps:")
        print("   1. Run unit tests: pytest tests/decision_engine/")
        print(
            "   2. Test with real data: python scripts/test_decision_engine_local.py"
        )
        print("   3. Configure thresholds based on your requirements")
        print("   4. Enable Pinecone integration for production")
    else:
        print("❌ Demo failed - check logs above")
        return 1

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
