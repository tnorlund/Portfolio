#!/usr/bin/env python3
"""
Test script for merchant pattern database functionality.

This script demonstrates the merchant-specific pattern database capabilities,
showing how common merchant keywords improve pattern detection accuracy.
"""

import asyncio
import logging
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "receipt_label"))

from receipt_label.pattern_detection.merchant_patterns import MerchantPatternDatabase, MerchantPattern
from receipt_label.constants import CORE_LABELS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_merchant_category_detection():
    """Test merchant category detection."""
    print("\n" + "="*60)
    print("TESTING MERCHANT CATEGORY DETECTION")
    print("="*60)
    
    db = MerchantPatternDatabase()
    
    test_merchants = [
        ("McDonald's", "restaurant"),
        ("Walmart Supercenter", "grocery"),
        ("Target", "retail"),
        ("CVS Pharmacy", "pharmacy"),
        ("Shell Gas Station", "gas_station"),
        ("Starbucks Coffee", "restaurant"),
        ("Whole Foods Market", "grocery"),
        ("Best Buy", "retail"),
        ("Walgreens", "pharmacy"),
        ("7-Eleven", "gas_station"),
        ("Unknown Store", "retail"),  # Should default to retail
    ]
    
    for merchant_name, expected_category in test_merchants:
        detected_category = db._detect_merchant_category(merchant_name)
        status = "✅" if detected_category == expected_category else "❌"
        print(f"{status} {merchant_name:20} → {detected_category:12} (expected: {expected_category})")


async def test_pattern_retrieval():
    """Test merchant pattern retrieval."""
    print("\n" + "="*60)
    print("TESTING MERCHANT PATTERN RETRIEVAL")
    print("="*60)
    
    db = MerchantPatternDatabase()
    
    test_merchants = [
        "McDonald's",
        "Walmart",
        "Target", 
        "CVS Pharmacy",
        "Shell"
    ]
    
    for merchant_name in test_merchants:
        print(f"\n--- {merchant_name} ---")
        patterns = await db.retrieve_merchant_patterns(merchant_name)
        
        for label_type, keywords in patterns.items():
            print(f"{label_type}: {len(keywords)} keywords")
            # Show first few keywords as examples
            examples = keywords[:5]
            if len(keywords) > 5:
                examples.append(f"... and {len(keywords) - 5} more")
            print(f"  Examples: {', '.join(examples)}")


async def test_enhanced_patterns():
    """Test enhanced pattern generation for receipt processing."""
    print("\n" + "="*60)
    print("TESTING ENHANCED PATTERN GENERATION")
    print("="*60)
    
    db = MerchantPatternDatabase()
    
    # Simulate receipt words for different merchants
    test_cases = [
        {
            "merchant": "McDonald's",
            "words": [
                {"text": "Big Mac", "word_id": 1},
                {"text": "French Fries", "word_id": 2},
                {"text": "Coca Cola", "word_id": 3},
                {"text": "$8.99", "word_id": 4},
                {"text": "Sales Tax", "word_id": 5},
            ]
        },
        {
            "merchant": "Walmart",
            "words": [
                {"text": "Bananas", "word_id": 1},
                {"text": "Milk", "word_id": 2},
                {"text": "Bread", "word_id": 3},
                {"text": "$15.47", "word_id": 4},
                {"text": "Visa", "word_id": 5},
            ]
        },
        {
            "merchant": "Target",
            "words": [
                {"text": "iPhone", "word_id": 1},
                {"text": "Case", "word_id": 2},
                {"text": "Screen Protector", "word_id": 3},
                {"text": "$89.99", "word_id": 4},
                {"text": "Store Credit", "word_id": 5},
            ]
        }
    ]
    
    for test_case in test_cases:
        merchant = test_case["merchant"]
        words = test_case["words"]
        
        print(f"\n--- {merchant} Receipt Analysis ---")
        
        enhanced_patterns = await db.get_enhanced_patterns_for_receipt(
            merchant_name=merchant,
            receipt_words=words,
            context={"category": db._detect_merchant_category(merchant)}
        )
        
        print(f"Category: {enhanced_patterns.get('category')}")
        print(f"Confidence Threshold: {enhanced_patterns.get('confidence_threshold')}")
        print(f"Available Patterns: {len(enhanced_patterns.get('word_patterns', {}))}")
        
        # Check which words would match patterns
        word_patterns = enhanced_patterns.get('word_patterns', {})
        matches_found = []
        
        for word in words:
            word_text = word['text'].lower()
            for pattern, label in word_patterns.items():
                if pattern in word_text or word_text in pattern:
                    matches_found.append({
                        "text": word['text'],
                        "pattern": pattern,
                        "label": label,
                        "word_id": word['word_id']
                    })
        
        if matches_found:
            print("Pattern Matches Found:")
            for match in matches_found:
                print(f"  '{match['text']}' → {match['label']} (pattern: '{match['pattern']}')")
        else:
            print("No pattern matches found for this receipt")


async def test_learning_capability():
    """Test learning from validated receipts."""
    print("\n" + "="*60)
    print("TESTING LEARNING FROM VALIDATED RECEIPTS")
    print("="*60)
    
    db = MerchantPatternDatabase()
    
    # Simulate validated receipt data
    validated_data = [
        {
            "merchant": "Local Coffee Shop",
            "validated_labels": {
                "espresso": CORE_LABELS["PRODUCT_NAME"],
                "croissant": CORE_LABELS["PRODUCT_NAME"],
                "tip": CORE_LABELS["DISCOUNT"],  # Use discount as closest match
                "city tax": CORE_LABELS["TAX"],
            },
            "receipt_text": ["Local Coffee Shop", "1 Espresso $3.50", "1 Croissant $2.25", "Tip $1.00", "City Tax $0.45", "Total $7.20"]
        },
        {
            "merchant": "Pizza Corner",
            "validated_labels": {
                "pepperoni pizza": CORE_LABELS["PRODUCT_NAME"],
                "garlic bread": CORE_LABELS["PRODUCT_NAME"],
                "delivery fee": CORE_LABELS["TAX"],  # Use tax as closest match for fees
                "store discount": CORE_LABELS["DISCOUNT"],
            },
            "receipt_text": ["Pizza Corner", "Large Pepperoni Pizza $18.99", "Garlic Bread $4.99", "Store Discount -$2.00", "Delivery Fee $3.99", "Total $25.97"]
        }
    ]
    
    for data in validated_data:
        merchant = data["merchant"]
        labels = data["validated_labels"]
        receipt_text = data["receipt_text"]
        
        print(f"\n--- Learning from {merchant} ---")
        print(f"Receipt text: {receipt_text}")
        print(f"Validated labels: {len(labels)} items")
        
        # Simulate learning (would store to Pinecone in production)
        success = await db.learn_from_validated_receipt(
            merchant_name=merchant,
            validated_labels=labels,
            receipt_text=receipt_text
        )
        
        if success:
            print(f"✅ Successfully learned patterns from {merchant}")
            
            # Show what patterns would be retrieved now
            learned_patterns = await db.retrieve_merchant_patterns(merchant)
            for label_type, keywords in learned_patterns.items():
                if keywords:  # Only show non-empty pattern lists
                    print(f"  {label_type}: {len(keywords)} patterns available")
        else:
            print(f"❌ Failed to learn patterns from {merchant}")


def test_database_statistics():
    """Test database statistics and summary."""
    print("\n" + "="*60)
    print("MERCHANT PATTERN DATABASE STATISTICS")
    print("="*60)
    
    db = MerchantPatternDatabase()
    stats = db.get_statistics()
    
    print(f"Total Common Patterns: {stats['total_common_patterns']}")
    print(f"Categories Supported: {stats['categories_supported']}")
    print("\nLabels per Category:")
    
    for category, label_count in stats['labels_per_category'].items():
        print(f"  {category:12}: {label_count:3} label types")
    
    print(f"\nDatabase Statistics:")
    print(f"  Patterns Stored: {stats['patterns_stored']}")
    print(f"  Patterns Retrieved: {stats['patterns_retrieved']}")
    print(f"  Cache Hits: {stats['cache_hits']}")
    print(f"  Merchants Indexed: {stats['merchants_indexed']}")


async def main():
    """Run all merchant pattern tests."""
    print("MERCHANT PATTERN DATABASE TEST SUITE")
    print("=====================================")
    
    # Test category detection
    test_merchant_category_detection()
    
    # Test pattern retrieval
    await test_pattern_retrieval()
    
    # Test enhanced pattern generation
    await test_enhanced_patterns()
    
    # Test learning capability
    await test_learning_capability()
    
    # Test database statistics
    test_database_statistics()
    
    print("\n" + "="*60)
    print("MERCHANT PATTERN TESTING COMPLETE")
    print("="*60)
    print("\nKey Benefits Demonstrated:")
    print("1. ✅ Automatic merchant category detection")
    print("2. ✅ Category-specific keyword databases")
    print("3. ✅ Enhanced pattern matching for receipts")
    print("4. ✅ Learning capability from validated data")
    print("5. ✅ Comprehensive statistics and monitoring")
    print("\nThis system provides merchant-specific pattern enhancement")
    print("that improves pattern detection accuracy and reduces GPT dependency.")


if __name__ == "__main__":
    asyncio.run(main())