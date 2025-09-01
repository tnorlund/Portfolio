#!/usr/bin/env python3
"""
Demo running receipt analysis in parallel using asyncio.
"""

import asyncio
import time
import logging
from typing import List, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.costco_analyzer import analyze_costco_receipt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def analyze_receipts_parallel(
    client: DynamoClient,
    receipts: List[Tuple[str, int]],
    update_labels: bool = False,
    dry_run: bool = True
):
    """Run receipt analysis in parallel."""
    
    print(f"🚀 Starting parallel analysis of {len(receipts)} receipts...")
    start_time = time.time()
    
    # Create tasks for all receipts
    tasks = [
        analyze_costco_receipt(
            client=client,
            image_id=image_id,
            receipt_id=receipt_id,
            update_labels=update_labels,
            dry_run=dry_run
        )
        for image_id, receipt_id in receipts
    ]
    
    # Run all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    elapsed = time.time() - start_time
    print(f"⏱️ Completed in {elapsed:.2f} seconds")
    
    # Process results
    successes = []
    errors = []
    
    for i, result in enumerate(results):
        receipt_id = f"{receipts[i][0]}/{receipts[i][1]}"
        if isinstance(result, Exception):
            logger.error(f"❌ {receipt_id}: {result}")
            errors.append((receipt_id, result))
        else:
            logger.info(f"✅ {receipt_id}: {len(result.discovered_labels)} labels, confidence {result.confidence_score:.2f}")
            successes.append(result)
    
    print(f"\n📊 Results: {len(successes)} successes, {len(errors)} errors")
    return successes, errors


async def main():
    """Demo parallel processing."""
    
    # Setup
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # Test receipts
    receipts = [
        ("6cd1f7f5-d988-4e11-9209-cb6535fc3b04", 1),
        ("314ac65b-2b97-45d6-81c2-e48fb0b8cef4", 1),
        ("a861f6a6-8d6d-42bc-907c-3330d8bd2022", 1),
        ("8388d1f1-b5d6-4560-b7dc-db273815dda1", 1),
    ]
    
    # Run analysis only (no label updates) in parallel
    successes, errors = await analyze_receipts_parallel(
        client, receipts, update_labels=False
    )
    
    # Show summary
    if successes:
        total_labels = sum(len(r.discovered_labels) for r in successes)
        avg_confidence = sum(r.confidence_score for r in successes) / len(successes)
        print(f"\n🎯 Total labels discovered: {total_labels}")
        print(f"📈 Average confidence: {avg_confidence:.3f}")


if __name__ == "__main__":
    asyncio.run(main())