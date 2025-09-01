#!/usr/bin/env python3
"""
Demo script showing how to use the PydanticOutputParser results to update receipt word labels.

This demonstrates the complete workflow:
1. Analyze receipt with LLM to get CurrencyLabel results
2. Match those results to actual receipt words in DynamoDB
3. Check existing labels and handle conflicts
4. Update/add labels as needed
"""

import asyncio
import logging
import os

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env

from receipt_label.costco_analyzer import analyze_costco_receipt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def demo_single_receipt_with_labels():
    """Demo the complete label updating workflow on a single receipt."""
    
    print("🏪 COSTCO RECEIPT LABEL UPDATE DEMO")
    print("=" * 80)
    print("This demo shows how LLM results get applied to actual receipt words in DynamoDB")
    print()
    
    # Setup environment
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # Choose a test receipt
    image_id = "6cd1f7f5-d988-4e11-9209-cb6535fc3b04"
    receipt_id = 1
    
    print(f"📋 Analyzing receipt: {image_id}/{receipt_id}")
    print()
    
    # Phase 1: DRY RUN - Show what would be updated
    print("🔍 PHASE 1: DRY RUN (showing what would be updated)")
    print("-" * 60)
    
    result_dry = await analyze_costco_receipt(
        client=client,
        image_id=image_id,
        receipt_id=receipt_id,
        update_labels=True,
        dry_run=True  # Don't actually make changes
    )
    
    print("\n" + "=" * 60)
    input("Press Enter to continue to Phase 2 (actual updates)...")
    print()
    
    # Phase 2: ACTUAL UPDATE - Apply the labels
    print("📝 PHASE 2: ACTUAL UPDATE (applying labels to DynamoDB)")
    print("-" * 60)
    
    result_real = await analyze_costco_receipt(
        client=client,
        image_id=image_id,
        receipt_id=receipt_id,
        update_labels=True,
        dry_run=False  # Actually make the changes
    )
    
    print("\n🎉 Demo complete!")
    print()
    print("Summary:")
    print(f"  - Found {len(result_real.discovered_labels)} currency labels")
    print(f"  - Confidence: {result_real.confidence_score:.2f}")
    print(f"  - Grand total: ${result_real.known_total:.2f}")


async def demo_analysis_only():
    """Demo just the analysis without updating labels."""
    
    print("🏪 COSTCO RECEIPT ANALYSIS ONLY (No Label Updates)")
    print("=" * 80)
    
    # Setup environment
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # Analyze all test receipts without updating labels
    costco_receipts = [
        ("6cd1f7f5-d988-4e11-9209-cb6535fc3b04", 1),
        ("314ac65b-2b97-45d6-81c2-e48fb0b8cef4", 1),
        ("a861f6a6-8d6d-42bc-907c-3330d8bd2022", 1),
        ("8388d1f1-b5d6-4560-b7dc-db273815dda1", 1),
    ]
    
    for image_id, receipt_id in costco_receipts:
        print(f"\n📋 Analyzing: {image_id}/{receipt_id}")
        try:
            result = await analyze_costco_receipt(
                client=client,
                image_id=image_id,
                receipt_id=receipt_id,
                update_labels=False  # Just analysis, no label updates
            )
            
            print(f"  ✅ Found {len(result.discovered_labels)} labels")
            print(f"  💰 Grand total: ${result.known_total:.2f}")
            print(f"  🎯 Confidence: {result.confidence_score:.2f}")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")


async def main():
    """Main demo function - choose which demo to run."""
    
    # Check environment setup
    if not os.getenv("OLLAMA_API_KEY") or not os.getenv("LANGCHAIN_API_KEY"):
        print("❌ Missing API keys:")
        print("   Set OLLAMA_API_KEY and LANGCHAIN_API_KEY environment variables")
        return
    
    print("Choose demo mode:")
    print("1. Full workflow with label updating (single receipt)")
    print("2. Analysis only (all test receipts)")
    print()
    
    choice = input("Enter choice (1 or 2): ").strip()
    
    if choice == "1":
        await demo_single_receipt_with_labels()
    elif choice == "2":
        await demo_analysis_only()
    else:
        print("Invalid choice. Running analysis-only demo.")
        await demo_analysis_only()


if __name__ == "__main__":
    asyncio.run(main())