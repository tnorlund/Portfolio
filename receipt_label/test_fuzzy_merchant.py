#!/usr/bin/env python3
"""
Test fuzzy merchant detection with practical thresholds.
"""

import asyncio
import json
from pathlib import Path

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine.enhanced_config import (
    create_practical_config,
)
from receipt_label.decision_engine.enhanced_integration import (
    EnhancedDecisionEngineOrchestrator,
    EnhancedReceiptContext,
)
from receipt_label.pattern_detection.fuzzy_merchant_detector import (
    FuzzyMerchantDetector,
)


async def test_fuzzy_merchant_detection():
    """Test fuzzy merchant detection on a real receipt."""

    # Load a receipt we know has metadata
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

    print("🧾 FUZZY MERCHANT DETECTION TEST")
    print("=" * 70)
    print(f"📍 Known merchant: {metadata['merchant_name']}")
    print(f"📍 Category: {metadata['merchant_category']}")
    print(f"📝 Receipt has {len(words)} words")

    # Debug: Check word format
    if words:
        print(f"\n🔍 DEBUG: Word format check")
        first_word = words[0]
        print(f"   Type: {type(first_word)}")
        print(
            f"   Has bounding_box attr: {hasattr(first_word, 'bounding_box')}"
        )
        if hasattr(first_word, "bounding_box"):
            bbox = first_word.bounding_box
            print(f"   Bounding box type: {type(bbox)}")
            print(f"   Bounding box value: {bbox}")
        print()

    # Test fuzzy merchant detection directly
    print("🔍 STEP 1: Direct Fuzzy Merchant Detection")
    print("-" * 50)

    detector = FuzzyMerchantDetector(min_similarity=75.0)
    matches = detector.detect_merchant(
        words=words,
        known_merchant=metadata["merchant_name"],
        metadata=metadata,
    )

    if matches:
        print(f"✅ Found {len(matches)} merchant matches:")
        for i, match in enumerate(matches):
            print(f"\n   Match {i+1}:")
            print(f"   - Extracted value: '{match.extracted_value}'")
            print(
                f"   - Matched text: '{match.metadata.get('matched_text', 'N/A')}'"
            )
            print(
                f"   - Similarity: {match.metadata.get('similarity_score', 0):.1f}%"
            )
            print(f"   - Type: {match.metadata.get('match_type', 'unknown')}")
            print(f"   - Confidence: {match.confidence:.2f}")
            if hasattr(match, "words") and match.words:
                try:
                    print(
                        f"   - Words: {' '.join(get_text(w) for w in match.words)}"
                    )
                except:
                    print(f"   - Words: <error getting word text>")
    else:
        print("❌ No fuzzy matches found")

    # Look at header area to see what's there
    print("\n📄 HEADER AREA TEXT (top 20% of receipt):")
    print("-" * 50)

    # Handle both object and dict format
    def get_y(word):
        if hasattr(word, "bounding_box"):
            # For objects, check if bounding_box is an object with .y or a dict
            bbox = word.bounding_box
            if hasattr(bbox, "y"):
                return bbox.y
            else:
                return bbox.get("y", 0)  # bounding_box is a dict
        else:
            # For dict format
            return word.get("bounding_box", {}).get("y", 0)

    def get_text(word):
        return word.text if hasattr(word, "text") else word.get("text", "")

    min_y = min(get_y(w) for w in words)
    max_y = max(get_y(w) for w in words)
    header_boundary = min_y + (max_y - min_y) * 0.2

    header_words = [w for w in words if get_y(w) <= header_boundary]
    header_text = " ".join(get_text(w) for w in header_words)
    print(f"Header text: {header_text[:200]}...")

    # Also search for the merchant name in all text
    print("\n🔍 Searching for merchant name in all text:")
    all_text = " ".join(get_text(w) for w in words)
    merchant_lower = metadata["merchant_name"].lower()

    # Look for exact matches
    if merchant_lower in all_text.lower():
        print(f"   ✅ Found exact match for '{metadata['merchant_name']}'")
    else:
        # Look for partial matches
        print(f"   ❌ No exact match found")
        # Check for individual words
        merchant_words = metadata["merchant_name"].split()
        for word in merchant_words:
            if word.lower() in all_text.lower():
                print(f"   🔸 Found partial match: '{word}'")

        # Show some text samples
        print(f"\n   First 200 chars of receipt: {all_text[:200]}...")
        print(
            f"   Text around position 500-700: {all_text[500:700] if len(all_text) > 700 else 'N/A'}"
        )

    # Test with practical decision engine
    print("\n\n🎯 STEP 2: Decision Engine with Practical Thresholds")
    print("=" * 70)

    # Create practical config
    config = create_practical_config()
    orchestrator = EnhancedDecisionEngineOrchestrator(config=config)

    # Test without metadata
    print("\n📊 Without metadata:")
    basic_context = {"image_id": image_id, "receipt_id": receipt_id}

    result_no_metadata = await orchestrator.process_receipt(
        words, basic_context
    )

    print(f"   Decision: {result_no_metadata.decision.action.value.upper()}")
    print(f"   Merchant: {result_no_metadata.decision.merchant_name}")
    print(
        f"   Coverage: {result_no_metadata.decision.coverage_percentage:.1f}%"
    )
    print(f"   Reasoning: {result_no_metadata.decision.reasoning}")

    # Test with metadata
    print("\n📊 With metadata and fuzzy matching:")
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

    print(f"   Decision: {result_with_metadata.decision.action.value.upper()}")
    print(f"   Merchant: {result_with_metadata.decision.merchant_name}")
    print(
        f"   Coverage: {result_with_metadata.decision.coverage_percentage:.1f}%"
    )
    print(f"   Reasoning: {result_with_metadata.decision.reasoning}")

    # Check essential fields
    print("\n🔑 Essential Fields Detection:")
    essential = result_with_metadata.decision.essential_fields_found
    # Check if it's a dict or set
    if isinstance(essential, dict):
        print(
            f"   MERCHANT_NAME: {'✅' if essential.get('MERCHANT_NAME') else '❌'}"
        )
        print(f"   DATE: {'✅' if essential.get('DATE') else '❌'}")
        print(
            f"   GRAND_TOTAL: {'✅' if essential.get('GRAND_TOTAL') else '❌'}"
        )
        print(
            f"   PRODUCT_NAME: {'✅' if essential.get('PRODUCT_NAME') else '❌'}"
        )
    elif isinstance(essential, set):
        print(
            f"   MERCHANT_NAME: {'✅' if 'MERCHANT_NAME' in essential else '❌'}"
        )
        print(f"   DATE: {'✅' if 'DATE' in essential else '❌'}")
        print(
            f"   GRAND_TOTAL: {'✅' if 'GRAND_TOTAL' in essential else '❌'}"
        )
        print(
            f"   PRODUCT_NAME: {'✅' if 'PRODUCT_NAME' in essential else '❌'}"
        )

    # Compare practical vs impractical thresholds
    print("\n\n📊 THRESHOLD COMPARISON")
    print("=" * 70)
    print("Old (impractical) thresholds:")
    print(f"   - Min coverage: 90%")
    print(f"   - Max unlabeled words: 5")
    print(f"   - Result: ALWAYS REQUIRED (unrealistic)")

    print("\nNew (practical) thresholds:")
    print(f"   - Focus on merchant name (MUST HAVE)")
    print(f"   - Min coverage: 30% (or category-specific)")
    print(f"   - Max unlabeled words: 100+ (realistic)")
    print(f"   - Result: Can actually SKIP or BATCH when appropriate")

    # Show the impact
    print("\n💡 KEY INSIGHT:")
    if result_with_metadata.decision.merchant_name:
        print(
            "✅ With fuzzy matching + metadata, we can detect the merchant name"
        )
        print("   even when it's not perfectly clear in the OCR text.")
        print(f"   This is CRITICAL because merchant name is required.")
    else:
        print(
            "❌ Without the merchant name, we CANNOT process the receipt properly."
        )


if __name__ == "__main__":
    asyncio.run(test_fuzzy_merchant_detection())
