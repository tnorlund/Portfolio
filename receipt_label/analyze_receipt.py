#!/usr/bin/env python3
"""
Simple CLI for merchant-agnostic receipt analysis.

Usage:
    python analyze_receipt.py --image-id <uuid> --receipt-id <id> [--dry-run] [--update-labels]
    python analyze_receipt.py --list-receipts [--merchant <name>]
"""

import argparse
import asyncio
import os
from typing import Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from n_parallel_analyzer import analyze_receipt_n_parallel


def setup_environment():
    """Check required environment variables."""
    required_vars = ["OLLAMA_API_KEY", "LANGCHAIN_API_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print("❌ Missing required environment variables:")
        for var in missing:
            print(f"   {var}")
        print("\nSet them with:")
        print("export OLLAMA_API_KEY=your_key_here")
        print("export LANGCHAIN_API_KEY=your_key_here")
        return False
    return True


def setup_langsmith_tracing(project_name: str = "receipt-label-analysis"):
    """Setup LangSmith tracing for LLM interactions."""
    langchain_api_key = os.getenv("LANGCHAIN_API_KEY")
    
    if langchain_api_key and langchain_api_key.strip():
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = project_name
        print("✅ LangSmith tracing enabled")
        print(f"   Project: {project_name}")
        print(f"   View traces at: https://smith.langchain.com/")
    else:
        print("⚠️ LANGCHAIN_API_KEY not set - tracing disabled")


def list_available_receipts(client: DynamoClient, merchant_filter: Optional[str] = None):
    """List available receipts in the database."""
    print("📋 AVAILABLE RECEIPTS")
    print("=" * 60)
    
    try:
        if merchant_filter:
            print(f"Filtering by merchant: {merchant_filter}")
            receipts, _ = client.get_receipt_metadatas_by_merchant(merchant_filter)
        else:
            receipts, _ = client.list_receipt_metadatas(limit=20)
        
        if not receipts:
            print("No receipts found.")
            return
        
        print(f"Found {len(receipts)} receipts:\n")
        
        for receipt in receipts:
            merchant = receipt.canonical_merchant_name or receipt.merchant_name or "Unknown"
            print(f"Image ID: {receipt.image_id}")
            print(f"Receipt ID: {receipt.receipt_id}")
            print(f"Merchant: {merchant}")
            print(f"Address: {receipt.address[:50]}..." if len(receipt.address) > 50 else f"Address: {receipt.address}")
            print(f"Date: {receipt.timestamp}")
            print("-" * 40)
            
    except Exception as e:
        print(f"Error listing receipts: {e}")


async def analyze_single_receipt(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    dry_run: bool = True,
    update_labels: bool = False
):
    """Analyze a single receipt."""
    
    print(f"🔍 ANALYZING RECEIPT: {image_id}/{receipt_id}")
    print("=" * 60)
    
    # Setup LangSmith tracing with dynamic project name
    setup_langsmith_tracing(f"receipt-analysis-{image_id[:8]}")
    
    # Check if receipt exists
    try:
        metadata = client.get_receipt_metadata(image_id, receipt_id)
        merchant = metadata.canonical_merchant_name or metadata.merchant_name or "Unknown"
        print(f"Merchant: {merchant}")
        print(f"Address: {metadata.address}")
        print()
    except Exception:
        print("⚠️  No metadata found for this receipt")
        print("   Proceeding with analysis anyway...")
        print()
    
    # Run analysis
    try:
        result = await analyze_receipt_n_parallel(
            client=client,
            image_id=image_id,
            receipt_id=receipt_id,
            update_labels=update_labels,
            dry_run=dry_run
        )
        
        print(f"\n✅ ANALYSIS COMPLETE!")
        print(f"   Labels discovered: {len(result.discovered_labels)}")
        print(f"   Processing time: {result.processing_time:.2f}s")
        print(f"   Average confidence: {result.confidence_score:.3f}")
        
        if update_labels:
            if dry_run:
                print("   Mode: DRY RUN (no database changes made)")
            else:
                print("   Mode: LIVE UPDATE (labels saved to database)")
        else:
            print("   Mode: ANALYSIS ONLY (no label updates)")
        
    except Exception as e:
        print(f"❌ Analysis failed: {e}")
        import traceback
        traceback.print_exc()


async def main():
    parser = argparse.ArgumentParser(
        description="Merchant-agnostic receipt analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available receipts
  python analyze_receipt.py --list-receipts
  
  # List receipts for specific merchant
  python analyze_receipt.py --list-receipts --merchant "Sprouts"
  
  # Analyze receipt (dry run)
  python analyze_receipt.py --image-id b6e7af49-3802-46f2-822e-bbd49cd55ada --receipt-id 1
  
  # Analyze and update labels in database
  python analyze_receipt.py --image-id b6e7af49-3802-46f2-822e-bbd49cd55ada --receipt-id 1 --update-labels
  
  # See what would be updated without making changes
  python analyze_receipt.py --image-id b6e7af49-3802-46f2-822e-bbd49cd55ada --receipt-id 1 --update-labels --dry-run
        """
    )
    
    # Main actions
    parser.add_argument("--list-receipts", action="store_true",
                       help="List available receipts in database")
    parser.add_argument("--image-id", type=str,
                       help="Receipt image UUID")
    parser.add_argument("--receipt-id", type=int,
                       help="Receipt ID number")
    
    # Options
    parser.add_argument("--merchant", type=str,
                       help="Filter receipts by merchant name")
    parser.add_argument("--update-labels", action="store_true",
                       help="Update labels in database (default: analysis only)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be updated without making changes")
    
    args = parser.parse_args()
    
    # Check environment
    if not setup_environment():
        return 1
    
    # Setup database connection
    try:
        env_vars = load_env()
        client = DynamoClient(env_vars.get("dynamodb_table_name"))
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return 1
    
    # Execute requested action
    if args.list_receipts:
        list_available_receipts(client, args.merchant)
        
    elif args.image_id and args.receipt_id:
        await analyze_single_receipt(
            client=client,
            image_id=args.image_id,
            receipt_id=args.receipt_id,
            dry_run=args.dry_run,
            update_labels=args.update_labels
        )
        
    else:
        parser.print_help()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))