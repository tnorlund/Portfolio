#!/usr/bin/env python3
"""
Demonstration of configurable decision engine thresholds.

This example shows how the decision engine now uses configurable
thresholds from environment variables or defaults, making it easier
to tune the system without code changes.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.config import DecisionEngineConfig, decision_engine_config
from receipt_label.agent.decision_engine import DecisionEngine, GPTDecision


def demo_configuration():
    """Demonstrate configuration system."""
    
    print("⚙️  Decision Engine Configuration Demo")
    print("=" * 50)
    
    # Show current configuration
    print("\n1. Current Configuration:")
    config_summary = DecisionEngineConfig.get_config_summary()
    
    for key, value in config_summary.items():
        print(f"   {key}: {value}")
    
    # Validate configuration
    print("\n2. Configuration Validation:")
    validation_error = DecisionEngineConfig.validate_config()
    if validation_error:
        print(f"   ❌ Validation failed: {validation_error}")
    else:
        print("   ✅ Configuration is valid")
    
    # Show environment variable usage
    print("\n3. Environment Variable Support:")
    print("   You can override any setting via environment variables:")
    print("   - DECISION_ENGINE_UNLABELED_THRESHOLD (default: 5)")
    print("   - DECISION_ENGINE_MIN_CONFIDENCE (default: 0.7)")
    print("   - DECISION_ENGINE_MAX_CORE_MISSING (default: 0.5)")
    print("   - DECISION_ENGINE_NOISE_MIN_LENGTH (default: 2)")
    print("   - DECISION_ENGINE_USE_MERCHANT_LABELS (default: true)")
    print("   - DECISION_ENGINE_SHADOW_TEST_PCT (default: 0.05)")


def demo_threshold_impact():
    """Demonstrate impact of threshold changes."""
    
    print("\n\n🔄 Threshold Impact Demo")
    print("=" * 50)
    
    # Create decision engine
    engine = DecisionEngine()
    
    # Sample receipt with varying unlabeled words
    test_cases = [
        {
            "name": "Few unlabeled words (3)",
            "receipt_words": [
                {"text": "McDonald's", "word_id": 1},
                {"text": "01/15/2024", "word_id": 2},
                {"text": "$25.99", "word_id": 3},
                {"text": "random1", "word_id": 4},
                {"text": "random2", "word_id": 5},
                {"text": "random3", "word_id": 6},
            ],
            "labeled_words": {
                1: {"label": "Merchant name", "confidence": 0.9},
                2: {"label": "The date the transaction occurred.", "confidence": 0.95},
                3: {"label": "Total amount for the entire transaction including taxes, tips, discounts.", "confidence": 0.98},
            }
        },
        {
            "name": "Exactly at threshold (5)",
            "receipt_words": [
                {"text": "Target", "word_id": 1},
                {"text": "2024-01-15", "word_id": 2},
                {"text": "Total", "word_id": 3},
                {"text": "$45.67", "word_id": 4},
                {"text": "item1", "word_id": 5},
                {"text": "item2", "word_id": 6},
                {"text": "item3", "word_id": 7},
                {"text": "item4", "word_id": 8},
                {"text": "item5", "word_id": 9},
            ],
            "labeled_words": {
                1: {"label": "Merchant name", "confidence": 0.85},
                2: {"label": "The date the transaction occurred.", "confidence": 0.9},
                4: {"label": "Total amount for the entire transaction including taxes, tips, discounts.", "confidence": 0.95},
            }
        },
        {
            "name": "Many unlabeled words (8)",
            "receipt_words": [
                {"text": "Store", "word_id": 1},
                {"text": "Date:", "word_id": 2},
                {"text": "01/15/24", "word_id": 3},
                {"text": "Total:", "word_id": 4},
                {"text": "$99.99", "word_id": 5},
                {"text": "unknown1", "word_id": 6},
                {"text": "unknown2", "word_id": 7},
                {"text": "unknown3", "word_id": 8},
                {"text": "unknown4", "word_id": 9},
                {"text": "unknown5", "word_id": 10},
                {"text": "unknown6", "word_id": 11},
                {"text": "unknown7", "word_id": 12},
                {"text": "unknown8", "word_id": 13},
            ],
            "labeled_words": {
                3: {"label": "The date the transaction occurred.", "confidence": 0.88},
                5: {"label": "Total amount for the entire transaction including taxes, tips, discounts.", "confidence": 0.92},
            }
        }
    ]
    
    print(f"\n📊 Current threshold: {engine.UNLABELED_THRESHOLD} meaningful unlabeled words")
    
    for test_case in test_cases:
        print(f"\n   {test_case['name']}:")
        
        decision, reason, unlabeled, missing = engine.make_smart_gpt_decision(
            test_case["receipt_words"],
            test_case["labeled_words"],
            {"merchant_name": test_case["labeled_words"].get(1, {}).get("label")}
        )
        
        print(f"   Decision: {decision.value}")
        print(f"   Reason: {reason}")
        print(f"   Unlabeled words: {len(unlabeled)}")


def demo_environment_override():
    """Demonstrate overriding configuration via environment."""
    
    print("\n\n🌍 Environment Override Demo")
    print("=" * 50)
    
    # Save original value
    original_threshold = decision_engine_config.MEANINGFUL_UNLABELED_WORDS_THRESHOLD
    
    print(f"\n1. Original threshold: {original_threshold}")
    
    # Simulate environment variable change
    print("\n2. Setting environment variable:")
    print("   export DECISION_ENGINE_UNLABELED_THRESHOLD=3")
    
    # Note: In a real scenario, you'd need to reload the module
    # Here we'll just show what would happen
    test_value = 3
    print(f"\n3. New threshold would be: {test_value}")
    print("   → More receipts would SKIP GPT (lower threshold)")
    print("   → Faster processing but might miss some labels")
    
    print("\n4. Setting higher threshold:")
    print("   export DECISION_ENGINE_UNLABELED_THRESHOLD=10")
    test_value = 10
    print(f"   New threshold would be: {test_value}")
    print("   → Fewer receipts would SKIP GPT (higher threshold)")
    print("   → Better coverage but more GPT calls")


def demo_noise_patterns():
    """Demonstrate noise pattern configuration."""
    
    print("\n\n🔇 Noise Pattern Configuration")
    print("=" * 50)
    
    print("\n1. Configurable Noise Patterns:")
    
    # Show noise patterns from config
    print("   Pattern types defined in DecisionEngineConfig.NOISE_PATTERNS:")
    for i, pattern in enumerate(DecisionEngineConfig.NOISE_PATTERNS, 1):
        print(f"   {i}. {pattern} - {describe_pattern(pattern)}")
    
    print(f"\n2. Minimum word length: {DecisionEngineConfig.NOISE_WORD_MIN_LENGTH}")
    print("   Words shorter than this are potential noise")
    
    # Test some words
    from receipt_label.utils.noise_detection import is_noise_word
    
    test_words = ["-", "--", "a", "ab", ".", "...", "*", "QTY", "$"]
    
    print("\n3. Noise Detection Examples:")
    for word in test_words:
        is_noise = is_noise_word(word)
        print(f"   '{word}' → {'Noise' if is_noise else 'Meaningful'}")


def describe_pattern(pattern: str) -> str:
    """Describe what a regex pattern matches."""
    descriptions = {
        r'^[a-z]$': "Single lowercase letter",
        r'^[0-9]$': "Single digit",
        r'^[^\w\s]+$': "Only special characters",
        r'^-+$': "One or more dashes",
        r'^\*+$': "One or more asterisks",
        r'^=+$': "One or more equals signs",
        r'^\|+$': "One or more vertical bars",
        r'^\.+$': "One or more dots",
        r'^_+$': "One or more underscores",
    }
    return descriptions.get(pattern, "Custom pattern")


def show_tuning_guide():
    """Show guide for tuning thresholds."""
    
    print("\n\n📖 Threshold Tuning Guide")
    print("=" * 50)
    
    print("\n1. MEANINGFUL_UNLABELED_WORDS_THRESHOLD:")
    print("   - Lower (3-4): Aggressive GPT skipping, faster but may miss labels")
    print("   - Default (5): Balanced approach")
    print("   - Higher (7-10): Conservative, more GPT calls but better coverage")
    
    print("\n2. MIN_PATTERN_CONFIDENCE:")
    print("   - Lower (0.5-0.6): Trust more patterns, risk false positives")
    print("   - Default (0.7): Good balance")
    print("   - Higher (0.8-0.9): Only trust high-quality patterns")
    
    print("\n3. SHADOW_TEST_PERCENTAGE:")
    print("   - Lower (0.01-0.03): Minimal validation overhead")
    print("   - Default (0.05): 5% validation for quality assurance")
    print("   - Higher (0.1-0.2): More validation, better metrics but slower")
    
    print("\n4. Best Practices:")
    print("   - Start with defaults and monitor metrics")
    print("   - Adjust based on false skip rates from shadow testing")
    print("   - Consider merchant-specific overrides for high-value merchants")
    print("   - Use A/B testing to validate threshold changes")


if __name__ == "__main__":
    print("🎛️  Configurable Thresholds Demo")
    print("=" * 70)
    print("\nThis demo shows how decision engine thresholds are now")
    print("configurable via environment variables and config classes.")
    print()
    
    # Run demonstrations
    demo_configuration()
    demo_threshold_impact()
    demo_environment_override()
    demo_noise_patterns()
    show_tuning_guide()
    
    print("\n\n✅ Demo completed successfully!")
    print("\n📝 Key Takeaways:")
    print("   • Thresholds are configurable without code changes")
    print("   • Environment variables override defaults")
    print("   • Configuration is validated for safety")
    print("   • Easy to tune for different use cases")
    print("   • Supports A/B testing different configurations")