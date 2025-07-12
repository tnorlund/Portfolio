#!/usr/bin/env python3
"""
Demonstration of data-driven merchant patterns using YAML configuration.

This example shows how merchant-specific patterns are now loaded from
a YAML file instead of being hardcoded, making them easier to maintain
and update without code changes.
"""

import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.pattern_detection.merchant_patterns import MerchantPatternDatabase


async def demo_yaml_patterns():
    """Demonstrate YAML-based merchant patterns."""
    
    print("📄 YAML-Based Merchant Patterns Demo")
    print("=" * 50)
    
    # Create merchant pattern database - will auto-load from YAML
    pattern_db = MerchantPatternDatabase()
    
    print("\n1. Patterns loaded from YAML file:")
    print(f"   File: {pattern_db.patterns_file}")
    
    # Get statistics
    stats = pattern_db.get_statistics()
    print(f"   Categories: {stats['categories_supported']}")
    print(f"   Total patterns: {stats['total_common_patterns']}")
    print("   Patterns per category:")
    for category, count in stats['labels_per_category'].items():
        print(f"   - {category}: {count} label types")
    
    # Example 2: Detect merchant category
    print("\n2. Merchant Category Detection:")
    test_merchants = [
        "McDonald's Restaurant",
        "Walmart Supercenter",
        "CVS Pharmacy",
        "Shell Gas Station",
        "Best Buy Electronics",
        "Whole Foods Market",
        "Starbucks Coffee"
    ]
    
    for merchant in test_merchants:
        category = pattern_db._detect_merchant_category(merchant)
        print(f"   {merchant} → {category}")
    
    # Example 3: Get patterns for specific merchant
    print("\n3. Getting Patterns for Specific Merchants:")
    
    for merchant_name in ["McDonald's", "Walmart", "CVS"]:
        patterns = await pattern_db.retrieve_merchant_patterns(merchant_name)
        print(f"\n   {merchant_name}:")
        for label_type, keywords in patterns.items():
            print(f"   - {label_type}: {len(keywords)} patterns")
            # Show first 5 patterns
            sample = keywords[:5]
            print(f"     Examples: {', '.join(sample)}")
    
    # Example 4: Demonstrate pattern reload
    print("\n4. Dynamic Pattern Reload:")
    
    # Create a custom patterns file
    custom_patterns = {
        "custom_restaurant": {
            "PRODUCT_NAME": ["custom burger", "custom fries", "custom shake"],
            "TAX": ["custom tax", "service charge"]
        },
        "merchant_categories": {
            "custom_restaurant": ["custom", "special"]
        }
    }
    
    import tempfile
    import yaml
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(custom_patterns, f)
        custom_file = f.name
    
    # Reload with custom patterns
    success = pattern_db.reload_patterns(custom_file)
    print(f"   Reload from custom file: {'✅ Success' if success else '❌ Failed'}")
    
    if success:
        # Test custom patterns
        custom_patterns = await pattern_db.retrieve_merchant_patterns("Custom Restaurant")
        print(f"   Custom patterns loaded: {len(custom_patterns)} label types")
    
    # Clean up
    Path(custom_file).unlink()
    
    # Example 5: Export patterns
    print("\n5. Export Patterns to YAML:")
    
    export_file = "exported_patterns.yaml"
    success = pattern_db.export_patterns_to_yaml(export_file)
    print(f"   Export to {export_file}: {'✅ Success' if success else '❌ Failed'}")
    
    if success and Path(export_file).exists():
        print(f"   File size: {Path(export_file).stat().st_size} bytes")
        # Clean up
        Path(export_file).unlink()
    
    return pattern_db


async def demo_pattern_usage():
    """Demonstrate how patterns are used in practice."""
    
    print("\n\n🔍 Pattern Usage Demo")
    print("=" * 50)
    
    pattern_db = MerchantPatternDatabase()
    
    # Simulate receipt processing
    print("\n1. Simulating Receipt Processing:")
    
    receipt_scenarios = [
        {
            "merchant": "McDonald's",
            "words": ["big", "mac", "french", "fries", "coca", "cola", "sales", "tax"]
        },
        {
            "merchant": "Walmart",
            "words": ["milk", "eggs", "bread", "laundry", "detergent", "visa", "****1234"]
        },
        {
            "merchant": "CVS Pharmacy",
            "words": ["prescription", "vitamins", "bandages", "total", "debit", "card"]
        }
    ]
    
    for scenario in receipt_scenarios:
        merchant = scenario["merchant"]
        words = scenario["words"]
        
        print(f"\n   Processing {merchant} receipt:")
        
        # Get patterns for merchant
        patterns = await pattern_db.retrieve_merchant_patterns(merchant)
        
        # Check which words match patterns
        matches = []
        for word in words:
            word_lower = word.lower()
            for label_type, keywords in patterns.items():
                if any(keyword in word_lower for keyword in keywords):
                    matches.append((word, label_type))
                    break
        
        print(f"   Words: {', '.join(words)}")
        print(f"   Matches found: {len(matches)}")
        for word, label in matches:
            print(f"   - '{word}' → {label}")


def show_yaml_benefits():
    """Show the benefits of YAML-based configuration."""
    
    print("\n\n✨ Benefits of YAML-Based Patterns")
    print("=" * 50)
    
    print("\n1. Easy to Edit:")
    print("   - No code changes required to add/remove patterns")
    print("   - Human-readable format")
    print("   - Version control friendly")
    
    print("\n2. Dynamic Updates:")
    print("   - Reload patterns without restarting application")
    print("   - A/B testing different pattern sets")
    print("   - Environment-specific configurations")
    
    print("\n3. Maintenance:")
    print("   - Non-developers can update patterns")
    print("   - Clear structure and organization")
    print("   - Easy to audit and review")
    
    print("\n4. Extensibility:")
    print("   - Add new categories without code changes")
    print("   - Custom patterns per deployment")
    print("   - Integration with external pattern sources")
    
    print("\n📁 Pattern File Location:")
    current_dir = Path(__file__).parent.parent
    yaml_file = current_dir / "pattern_detection" / "merchant_patterns.yaml"
    print(f"   {yaml_file}")
    
    if yaml_file.exists():
        print(f"   Size: {yaml_file.stat().st_size:,} bytes")
        
        # Count patterns
        import yaml
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        
        total_patterns = 0
        for category, labels in data.items():
            if category != "merchant_categories":
                for label_type, keywords in labels.items():
                    total_patterns += len(keywords)
        
        print(f"   Total patterns defined: {total_patterns}")


if __name__ == "__main__":
    print("🏪 Merchant Patterns YAML Configuration Demo")
    print("=" * 70)
    print("\nThis demo shows how merchant patterns are now data-driven")
    print("using YAML configuration instead of hardcoded values.")
    print()
    
    # Run async demos
    asyncio.run(demo_yaml_patterns())
    asyncio.run(demo_pattern_usage())
    
    # Show benefits
    show_yaml_benefits()
    
    print("\n\n✅ Demo completed successfully!")
    print("\n📝 Key Takeaways:")
    print("   • Patterns are loaded from merchant_patterns.yaml")
    print("   • Categories and keywords are configurable")
    print("   • Patterns can be reloaded dynamically")
    print("   • Easy to maintain and extend without code changes")
    print("   • Export functionality for pattern management")