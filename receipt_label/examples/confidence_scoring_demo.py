#!/usr/bin/env python3
"""
Demonstration of confidence scoring in semantic mapping.

This example shows how the semantic mapper calculates confidence scores
based on multiple factors: base pattern confidence, semantic mapping quality,
contextual clues, and merchant-specific knowledge.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.pattern_detection.semantic_mapper import SemanticMapper, ConfidenceScore
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_dynamo.entities import ReceiptWord


def create_test_pattern_match(
    text: str, 
    pattern_type: PatternType, 
    confidence: float,
    line_id: int = 0,
    word_id: int = 1
) -> PatternMatch:
    """Create a test pattern match."""
    word = ReceiptWord(
        receipt_id=1,
        image_id="550e8400-e29b-41d4-a716-446655440000",  # Valid UUID
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
        top_left={"x": 0, "y": 0},
        top_right={"x": 100, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 100, "y": 20},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95
    )
    
    return PatternMatch(
        word=word,
        pattern_type=pattern_type,
        confidence=confidence,
        matched_text=text,
        extracted_value=text,
        metadata={"text": text}
    )


def demo_confidence_scoring():
    """Demonstrate confidence scoring functionality."""
    
    print("🎯 Confidence Scoring Demo")
    print("=" * 50)
    
    # Create semantic mapper
    mapper = SemanticMapper()
    
    # Example 1: Basic confidence scoring
    print("\n1. Basic Confidence Scoring:")
    
    test_matches = [
        create_test_pattern_match("$49.99", PatternType.GRAND_TOTAL, 0.92, line_id=15),
        create_test_pattern_match("01/15/2024", PatternType.DATE, 0.88, line_id=2),
        create_test_pattern_match("McDonald's", PatternType.MERCHANT_NAME, 0.75, line_id=1),
        create_test_pattern_match("Big Mac", PatternType.PRODUCT_NAME, 0.65, line_id=8),
    ]
    
    labeled_words = mapper.map_patterns_to_labels(test_matches)
    
    for word_id, label_info in labeled_words.items():
        score = label_info["confidence_score"]
        print(f"\n   Pattern: {label_info['pattern_type']}")
        print(f"   Text: '{label_info['metadata']['text']}'")
        print(f"   Label: {label_info['label']}")
        print(f"   Confidence Breakdown:")
        print(f"   - Base confidence: {score.base_confidence:.2f}")
        print(f"   - Semantic confidence: {score.semantic_confidence:.2f}")
        print(f"   - Context boost: {score.context_boost:.2f}")
        print(f"   - Merchant boost: {score.merchant_boost:.2f}")
        print(f"   → Final confidence: {score.final_confidence:.2f}")
    
    # Example 2: Confidence distribution
    print("\n2. Confidence Distribution:")
    
    distribution = mapper.get_confidence_distribution(labeled_words)
    print(f"   High confidence (≥0.85): {distribution['high']} labels")
    print(f"   Medium confidence (0.5-0.85): {distribution['medium']} labels")
    print(f"   Low confidence (<0.5): {distribution['low']} labels")
    
    # Example 3: Filtering by confidence
    print("\n3. Filtering by Confidence:")
    
    min_confidence = 0.7
    filtered = mapper.filter_by_confidence(labeled_words, min_confidence)
    print(f"   Original labels: {len(labeled_words)}")
    print(f"   After filtering (≥{min_confidence}): {len(filtered)}")
    print(f"   Filtered labels: {[info['label'] for info in filtered.values()]}")
    
    # Example 4: Context boost demonstration
    print("\n4. Context Boost Examples:")
    
    context_examples = [
        # Merchant at top gets boost
        create_test_pattern_match("Target", PatternType.MERCHANT_NAME, 0.7, line_id=0),
        # Merchant at bottom gets no boost
        create_test_pattern_match("Target", PatternType.MERCHANT_NAME, 0.7, line_id=20),
        # Total at bottom gets boost
        create_test_pattern_match("$99.99", PatternType.GRAND_TOTAL, 0.8, line_id=25),
        # Total at top gets no boost
        create_test_pattern_match("$99.99", PatternType.GRAND_TOTAL, 0.8, line_id=1),
    ]
    
    for match in context_examples:
        score = mapper._calculate_confidence_score(match)
        print(f"\n   {match.pattern_type.name} at line {match.word.line_id}:")
        print(f"   Base: {score.base_confidence:.2f}, Context boost: {score.context_boost:.2f}")
        print(f"   Final confidence: {score.final_confidence:.2f}")
    
    # Example 5: Merchant pattern enhancement
    print("\n5. Merchant Pattern Enhancement:")
    
    # Original labels
    original_matches = [
        create_test_pattern_match("fries", PatternType.PRODUCT_NAME, 0.6),
        create_test_pattern_match("coke", PatternType.PRODUCT_NAME, 0.5),
    ]
    
    labeled = mapper.map_patterns_to_labels(original_matches)
    
    print("\n   Before merchant patterns:")
    for word_id, info in labeled.items():
        print(f"   - '{info['metadata']['text']}': confidence {info['confidence']:.2f}")
    
    # Apply merchant patterns
    merchant_patterns = {
        "french fries": {"label": "Descriptive text of a purchased product (item name).", "confidence": 0.9},
        "coca cola": {"label": "Descriptive text of a purchased product (item name).", "confidence": 0.95},
    }
    
    enhanced = mapper.enhance_merchant_patterns(labeled.copy(), merchant_patterns)
    
    print("\n   After merchant patterns:")
    for word_id, info in enhanced.items():
        if info.get("source") == "merchant_patterns":
            print(f"   - '{info['metadata']['text']}': confidence {info['confidence']:.2f} ✨ (merchant boost)")
    
    # Example 6: Statistics
    print("\n6. Mapping Statistics:")
    stats = mapper.get_statistics()
    
    print(f"   Total patterns processed: {stats['total_patterns']}")
    print(f"   Successfully mapped: {stats['mapped_patterns']}")
    print(f"   Essential labels found: {stats['essential_labels_found']}")
    print(f"   High confidence labels: {stats['high_confidence_labels']}")
    print(f"   Low confidence labels: {stats['low_confidence_labels']}")
    if stats['total_patterns'] > 0:
        print(f"   Mapping rate: {stats['mapping_rate']:.1%}")
        print(f"   Essential rate: {stats['essential_rate']:.1%}")


def demo_confidence_factors():
    """Demonstrate how different factors affect confidence."""
    
    print("\n\n📊 Confidence Factor Analysis")
    print("=" * 50)
    
    # Show how each factor contributes
    print("\n1. Factor Weights:")
    print("   - Base confidence: 40%")
    print("   - Semantic mapping: 30%")
    print("   - Context clues: 20%")
    print("   - Merchant knowledge: 10%")
    
    # Example calculation
    print("\n2. Example Calculation:")
    
    example_score = ConfidenceScore(
        base_confidence=0.85,      # Pattern detector confidence
        semantic_confidence=0.90,  # How well pattern maps to label
        context_boost=0.30,        # Position is ideal
        merchant_boost=0.20        # Merchant-specific pattern
    )
    
    print(f"   Base (0.85 × 0.4) = {0.85 * 0.4:.3f}")
    print(f"   Semantic (0.90 × 0.3) = {0.90 * 0.3:.3f}")
    print(f"   Context (0.30 × 0.2) = {0.30 * 0.2:.3f}")
    print(f"   Merchant (0.20 × 0.1) = {0.20 * 0.1:.3f}")
    print(f"   ─────────────────────────")
    print(f"   Final confidence = {example_score.final_confidence:.3f}")
    
    # Semantic confidence map
    print("\n3. Semantic Confidence Map (Pattern → Label):")
    mapper = SemanticMapper()
    
    high_confidence = []
    medium_confidence = []
    low_confidence = []
    
    for pattern_type, conf in mapper.semantic_confidence_map.items():
        if conf >= 0.9:
            high_confidence.append((pattern_type, conf))
        elif conf >= 0.8:
            medium_confidence.append((pattern_type, conf))
        else:
            low_confidence.append((pattern_type, conf))
    
    print("\n   High confidence mappings (≥0.9):")
    for pt, conf in sorted(high_confidence, key=lambda x: x[1], reverse=True):
        print(f"   - {pt.name}: {conf:.2f}")
    
    print("\n   Medium confidence mappings (0.8-0.9):")
    for pt, conf in sorted(medium_confidence, key=lambda x: x[1], reverse=True):
        print(f"   - {pt.name}: {conf:.2f}")
    
    print("\n   Lower confidence mappings (<0.8):")
    for pt, conf in sorted(low_confidence, key=lambda x: x[1], reverse=True):
        print(f"   - {pt.name}: {conf:.2f}")


def show_benefits():
    """Show benefits of confidence scoring."""
    
    print("\n\n✨ Benefits of Confidence Scoring")
    print("=" * 50)
    
    print("\n1. Better Decision Making:")
    print("   - Prioritize high-confidence labels for processing")
    print("   - Route low-confidence items for manual review")
    print("   - Reduce false positives in automated systems")
    
    print("\n2. Quality Metrics:")
    print("   - Track labeling quality over time")
    print("   - Identify patterns that need improvement")
    print("   - Measure impact of merchant-specific training")
    
    print("\n3. Cost Optimization:")
    print("   - Skip GPT for high-confidence essential labels")
    print("   - Batch process medium-confidence items")
    print("   - Focus GPT on truly ambiguous cases")
    
    print("\n4. Continuous Improvement:")
    print("   - Learn from confidence patterns")
    print("   - Adjust weights based on outcomes")
    print("   - Fine-tune context rules per merchant")


if __name__ == "__main__":
    print("🔍 Semantic Mapper Confidence Scoring Demo")
    print("=" * 70)
    print("\nThis demo shows how confidence scoring improves label quality")
    print("by considering multiple factors beyond raw pattern matching.")
    print()
    
    # Run demos
    demo_confidence_scoring()
    demo_confidence_factors()
    show_benefits()
    
    print("\n\n✅ Demo completed successfully!")
    print("\n📝 Key Takeaways:")
    print("   • Confidence scores combine multiple factors for accuracy")
    print("   • Context (position, format) significantly affects confidence")
    print("   • Merchant-specific patterns boost confidence")
    print("   • Filtering by confidence improves processing quality")
    print("   • Statistics help monitor and improve the system")