#!/usr/bin/env python3
"""
Quick test to verify Google Places metadata impact on pattern detection.
"""

import asyncio
import json
from pathlib import Path

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine import DecisionEngineConfig
from receipt_label.decision_engine.enhanced_integration import (
    EnhancedDecisionEngineOrchestrator,
    EnhancedReceiptContext,
)


async def test_single_receipt():
    """Test a single receipt with metadata to see the impact."""

    # Load Sprouts receipt that we know has metadata
    receipt_file = Path(
        "./receipt_data_with_labels/cee0fbe1-d84a-4f69-9af1-7166226e8b88.json"
    )

    with open(receipt_file, "r") as f:
        export_data = json.load(f)

    # Get data
    receipt = export_data["receipts"][0]
    metadata = export_data["receipt_metadatas"][0]
    image_id = receipt["image_id"]
    receipt_id = receipt["receipt_id"]
    words_data = [
        w
        for w in export_data["receipt_words"]
        if w["image_id"] == image_id and w["receipt_id"] == receipt_id
    ]

    # Create receipt objects
    receipt_obj, words, lines = create_mock_receipt_from_export(
        {
            "receipt": receipt,
            "words": words_data,
            "lines": export_data["receipt_lines"],
        }
    )

    print(f"🧾 Testing receipt: {image_id}")
    print(
        f"📍 Metadata: {metadata['merchant_name']} ({metadata['merchant_category']})"
    )
    print(f"📍 Address: {metadata['address']}")
    print(f"📍 Phone: {metadata['phone_number']}")
    print(f"📍 Validated by: {metadata['validated_by']}")
    print(f"\n📝 Receipt has {len(words)} words\n")

    # Initialize orchestrator with 100% rollout for testing
    config = DecisionEngineConfig(
        enabled=True, rollout_percentage=100.0  # Enable for all receipts
    )
    orchestrator = EnhancedDecisionEngineOrchestrator(
        config=config, optimization_level="advanced"
    )

    # Test WITHOUT metadata
    print("=" * 70)
    print("🔍 TEST 1: Without metadata")
    print("=" * 70)

    basic_context = {"image_id": image_id, "receipt_id": receipt_id}

    result_no_metadata = await orchestrator.process_receipt(
        words, basic_context
    )

    print(f"✅ Decision: {result_no_metadata.decision.action.value.upper()}")
    print(f"   Confidence: {result_no_metadata.decision.confidence.value}")
    print(f"   Merchant detected: {result_no_metadata.decision.merchant_name}")
    print(
        f"   Coverage: {result_no_metadata.decision.coverage_percentage:.1f}%"
    )
    print(f"   Reasoning: {result_no_metadata.decision.reasoning}")

    # Print pattern results
    print(f"\n📊 Pattern Detection Results:")
    for pattern_type, patterns in result_no_metadata.pattern_results.items():
        if pattern_type != "_metadata" and patterns:
            print(f"   {pattern_type}: {len(patterns)} patterns found")

    # Test WITH metadata using enhanced integration
    print("\n" + "=" * 70)
    print("🔍 TEST 2: With Google Places metadata (Enhanced)")
    print("=" * 70)

    enhanced_context = EnhancedReceiptContext(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=metadata["merchant_name"],
        merchant_category=metadata["merchant_category"],
        address=metadata["address"],
        phone_number=metadata["phone_number"],
        place_id=metadata["place_id"],
        validated_by=metadata["validated_by"],
    )

    result_with_metadata = await orchestrator.process_receipt_with_metadata(
        words, enhanced_context
    )

    print(f"✅ Decision: {result_with_metadata.decision.action.value.upper()}")
    print(f"   Confidence: {result_with_metadata.decision.confidence.value}")
    print(
        f"   Merchant detected: {result_with_metadata.decision.merchant_name}"
    )
    print(
        f"   Coverage: {result_with_metadata.decision.coverage_percentage:.1f}%"
    )
    print(f"   Reasoning: {result_with_metadata.decision.reasoning}")

    # Print pattern results
    print(f"\n📊 Pattern Detection Results:")
    for pattern_type, patterns in result_with_metadata.pattern_results.items():
        if pattern_type != "_metadata" and patterns:
            print(f"   {pattern_type}: {len(patterns)} patterns found")
            if pattern_type == "merchant":
                for p in patterns[:3]:  # Show first 3
                    if hasattr(p, "extracted_value"):
                        print(
                            f"      - {p.extracted_value} (confidence: {p.confidence:.2f})"
                        )

    # Compare the impact
    print("\n" + "=" * 70)
    print("📊 METADATA IMPACT SUMMARY")
    print("=" * 70)

    decision_improved = (
        result_no_metadata.decision.action.value
        != result_with_metadata.decision.action.value
    )
    merchant_detected = (
        not result_no_metadata.decision.merchant_name
        and result_with_metadata.decision.merchant_name
    )

    print(f"✨ Decision improved: {'YES' if decision_improved else 'NO'}")
    print(f"✨ Merchant now detected: {'YES' if merchant_detected else 'NO'}")
    print(
        f"✨ Coverage change: {result_no_metadata.decision.coverage_percentage:.1f}% → {result_with_metadata.decision.coverage_percentage:.1f}%"
    )

    if result_with_metadata.decision.action.value == "skip":
        print(f"\n💰 COST SAVINGS: This receipt would skip GPT processing!")


if __name__ == "__main__":
    asyncio.run(test_single_receipt())
