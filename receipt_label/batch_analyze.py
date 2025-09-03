#!/usr/bin/env python3
"""
Batch analysis script for processing multiple receipts with merchant-agnostic N-parallel analyzer.

Usage:
    python batch_analyze.py [--merchant <name>] [--limit <num>] [--update-labels] [--dry-run] [--parallel <num>]
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

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
        return False
    return True


def setup_langsmith_tracing(project_name: str = "batch-receipt-analysis"):
    """Setup LangSmith tracing for batch processing."""
    langchain_api_key = os.getenv("LANGCHAIN_API_KEY")
    
    if langchain_api_key and langchain_api_key.strip():
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = project_name
        print("✅ LangSmith tracing enabled")
        print(f"   Project: {project_name}")
        print(f"   View traces at: https://smith.langchain.com/")
    else:
        print("⚠️ LANGCHAIN_API_KEY not set - tracing disabled")


def get_receipts_to_process(
    client: DynamoClient, 
    merchant_filter: Optional[str] = None,
    limit: Optional[int] = None
) -> List[tuple]:
    """Get list of receipts to process."""
    print("📋 Finding receipts to process...")
    
    try:
        if merchant_filter:
            print(f"   Filtering by merchant: {merchant_filter}")
            receipts, _ = client.get_receipt_metadatas_by_merchant(merchant_filter, limit=limit)
        else:
            print(f"   Getting all receipts (limit: {limit or 'none'})")
            receipts, _ = client.list_receipt_metadatas(limit=limit)
        
        receipt_list = [(r.image_id, r.receipt_id, r.canonical_merchant_name or r.merchant_name or "Unknown") 
                       for r in receipts]
        
        print(f"   Found {len(receipt_list)} receipts to process")
        return receipt_list
        
    except Exception as e:
        print(f"   Error getting receipts: {e}")
        return []


async def analyze_receipt_with_error_handling(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    merchant_name: str,
    update_labels: bool = False,
    dry_run: bool = True
) -> Dict[str, Any]:
    """Analyze a single receipt with comprehensive error handling."""
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    start_time = time.time()
    
    try:
        result = await analyze_receipt_n_parallel(
            client=client,
            image_id=image_id,
            receipt_id=receipt_id,
            update_labels=update_labels,
            dry_run=dry_run
        )
        
        processing_time = time.time() - start_time
        
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "status": "success",
            "labels_discovered": len(result.discovered_labels),
            "confidence_score": result.confidence_score,
            "processing_time": processing_time,
            "analysis_timestamp": datetime.now().isoformat(),
        }
        
    except Exception as e:
        processing_time = time.time() - start_time
        error_msg = str(e)
        
        print(f"❌ {receipt_identifier} ({merchant_name}) failed: {error_msg}")
        
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "status": "failed",
            "error": error_msg,
            "processing_time": processing_time,
            "analysis_timestamp": datetime.now().isoformat(),
        }


async def process_receipts_batch(
    client: DynamoClient,
    receipts: List[tuple],
    update_labels: bool = False,
    dry_run: bool = True,
    max_parallel: int = 3
) -> List[Dict[str, Any]]:
    """Process receipts in parallel batches."""
    
    results = []
    total_receipts = len(receipts)
    
    print(f"\n🚀 BATCH PROCESSING {total_receipts} RECEIPTS")
    print(f"   Max parallel: {max_parallel}")
    print(f"   Update labels: {update_labels}")
    print(f"   Dry run: {dry_run if update_labels else 'N/A (analysis only)'}")
    print("=" * 80)
    
    # Process in batches to avoid overwhelming the system
    for i in range(0, total_receipts, max_parallel):
        batch_end = min(i + max_parallel, total_receipts)
        batch_receipts = receipts[i:batch_end]
        
        print(f"\n📦 Processing batch {i//max_parallel + 1}: receipts {i+1}-{batch_end}")
        
        # Create tasks for parallel processing
        tasks = [
            analyze_receipt_with_error_handling(
                client, image_id, receipt_id, merchant_name, update_labels, dry_run
            )
            for image_id, receipt_id, merchant_name in batch_receipts
        ]
        
        # Execute batch in parallel
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle any exceptions that weren't caught
        for j, result in enumerate(batch_results):
            if isinstance(result, Exception):
                image_id, receipt_id, merchant_name = batch_receipts[j]
                result = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant_name": merchant_name,
                    "status": "failed",
                    "error": str(result),
                    "processing_time": 0.0,
                    "analysis_timestamp": datetime.now().isoformat(),
                }
            
            results.append(result)
            
            # Show progress
            status_emoji = "✅" if result["status"] == "success" else "❌"
            merchant = result["merchant_name"][:20]
            processing_time = result.get("processing_time", 0.0)
            labels_count = result.get("labels_discovered", 0)
            
            if result["status"] == "success":
                print(f"   {status_emoji} {result['image_id'][:8]} ({merchant}) - {labels_count} labels in {processing_time:.1f}s")
            else:
                print(f"   {status_emoji} {result['image_id'][:8]} ({merchant}) - {result.get('error', 'Unknown error')}")
    
    return results


def save_batch_results(results: List[Dict[str, Any]], output_file: str):
    """Save batch results to JSON file."""
    
    # Calculate summary statistics
    successful = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]
    
    total_labels = sum(r.get("labels_discovered", 0) for r in successful)
    total_time = sum(r.get("processing_time", 0.0) for r in results)
    avg_time = total_time / len(results) if results else 0
    
    summary = {
        "batch_info": {
            "total_receipts": len(results),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": len(successful) / len(results) * 100 if results else 0,
            "total_labels_discovered": total_labels,
            "total_time": total_time,
            "avg_time_per_receipt": avg_time,
            "timestamp": datetime.now().isoformat()
        },
        "results": results
    }
    
    with open(output_file, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"\n📊 BATCH ANALYSIS COMPLETE")
    print(f"   Total receipts: {len(results)}")
    print(f"   Successful: {len(successful)} ({len(successful)/len(results)*100:.1f}%)")
    print(f"   Failed: {len(failed)} ({len(failed)/len(results)*100:.1f}%)")
    print(f"   Total labels discovered: {total_labels}")
    print(f"   Total time: {total_time:.1f}s")
    print(f"   Average time per receipt: {avg_time:.1f}s")
    print(f"   Results saved to: {output_file}")


async def main():
    parser = argparse.ArgumentParser(
        description="Batch merchant-agnostic receipt analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze 10 receipts (dry run)
  python batch_analyze.py --limit 10
  
  # Analyze all Sprouts receipts
  python batch_analyze.py --merchant "Sprouts"
  
  # Update labels for 5 receipts (dry run to see what would change)
  python batch_analyze.py --limit 5 --update-labels --dry-run
  
  # Actually update labels for Walmart receipts
  python batch_analyze.py --merchant "Walmart" --update-labels
  
  # Process 50 receipts with higher parallelism
  python batch_analyze.py --limit 50 --parallel 5
        """
    )
    
    # Main options
    parser.add_argument("--merchant", type=str,
                       help="Filter receipts by merchant name")
    parser.add_argument("--limit", type=int,
                       help="Maximum number of receipts to process")
    parser.add_argument("--update-labels", action="store_true",
                       help="Update labels in database (default: analysis only)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be updated without making changes")
    parser.add_argument("--parallel", type=int, default=3,
                       help="Maximum parallel receipts to process (default: 3)")
    parser.add_argument("--output", type=str,
                       help="Output file for results (default: auto-generated)")
    
    args = parser.parse_args()
    
    # Check environment
    if not setup_environment():
        return 1
    
    # Setup tracing
    project_name = f"batch-analysis-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if args.merchant:
        project_name = f"batch-{args.merchant.lower().replace(' ', '-')}-{datetime.now().strftime('%H%M%S')}"
    setup_langsmith_tracing(project_name)
    
    # Setup database connection
    try:
        env_vars = load_env()
        client = DynamoClient(env_vars.get("dynamodb_table_name"))
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return 1
    
    # Get receipts to process
    receipts = get_receipts_to_process(client, args.merchant, args.limit)
    if not receipts:
        print("❌ No receipts found to process")
        return 1
    
    # Process receipts
    start_time = time.time()
    results = await process_receipts_batch(
        client=client,
        receipts=receipts,
        update_labels=args.update_labels,
        dry_run=args.dry_run,
        max_parallel=args.parallel
    )
    total_time = time.time() - start_time
    
    # Generate output filename
    if not args.output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        merchant_suffix = f"_{args.merchant.lower().replace(' ', '_')}" if args.merchant else ""
        mode_suffix = "_update" if args.update_labels else "_analysis"
        args.output = f"batch_results{merchant_suffix}{mode_suffix}_{timestamp}.json"
    
    # Save results
    save_batch_results(results, args.output)
    
    print(f"\n🎉 Batch processing complete in {total_time:.1f}s")
    print(f"📊 Results available in: {args.output}")
    
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))