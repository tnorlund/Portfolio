#!/usr/bin/env python3
"""
Demo running receipt analysis in parallel with concurrency limits.
"""

import asyncio
import time
from typing import List, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.costco_analyzer import analyze_costco_receipt


async def analyze_with_semaphore(
    semaphore: asyncio.Semaphore,
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    **kwargs
):
    """Analyze receipt with semaphore for concurrency control."""
    async with semaphore:
        return await analyze_costco_receipt(client, image_id, receipt_id, **kwargs)


async def batch_analyze_receipts(
    client: DynamoClient,
    receipts: List[Tuple[str, int]],
    max_concurrent: int = 3,  # Adjust based on API rate limits
    **kwargs
):
    """Analyze receipts with controlled concurrency."""
    
    print(f"🚀 Analyzing {len(receipts)} receipts with max {max_concurrent} concurrent...")
    
    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(max_concurrent)
    
    start_time = time.time()
    
    # Create tasks with semaphore
    tasks = [
        analyze_with_semaphore(
            semaphore, client, image_id, receipt_id, **kwargs
        )
        for image_id, receipt_id in receipts
    ]
    
    # Execute with progress tracking
    results = []
    for i, coro in enumerate(asyncio.as_completed(tasks)):
        try:
            result = await coro
            results.append(result)
            print(f"✅ Completed {i+1}/{len(tasks)}: {len(result.discovered_labels)} labels")
        except Exception as e:
            print(f"❌ Error {i+1}/{len(tasks)}: {e}")
            results.append(None)
    
    elapsed = time.time() - start_time
    print(f"⏱️ Total time: {elapsed:.2f}s ({elapsed/len(receipts):.2f}s per receipt)")
    
    return [r for r in results if r is not None]


async def main():
    """Demo with controlled concurrency."""
    
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    receipts = [
        ("6cd1f7f5-d988-4e11-9209-cb6535fc3b04", 1),
        ("314ac65b-2b97-45d6-81c2-e48fb0b8cef4", 1),
        ("a861f6a6-8d6d-42bc-907c-3330d8bd2022", 1),
        ("8388d1f1-b5d6-4560-b7dc-db273815dda1", 1),
    ]
    
    # Test different concurrency levels
    for max_concurrent in [1, 2, 4]:
        print(f"\n📊 Testing with max_concurrent={max_concurrent}")
        results = await batch_analyze_receipts(
            client, receipts, max_concurrent=max_concurrent, update_labels=False
        )
        print(f"   Results: {len(results)} successful")


if __name__ == "__main__":
    asyncio.run(main())