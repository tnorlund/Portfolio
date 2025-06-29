#!/usr/bin/env python3
"""
Demonstration of AI Usage Context Manager Patterns (Phase 3).

This example shows how to use the context manager and decorator patterns
for automatic AI usage tracking in production code.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock environment for demo
os.environ["ENVIRONMENT"] = "development"
os.environ["DYNAMODB_TABLE_NAME"] = "AIUsageMetrics"
os.environ["USER_ID"] = "demo-user"

from receipt_label.utils import (
    ai_usage_context,
    ai_usage_tracked,
    batch_ai_usage_context,
    get_current_context,
    partial_failure_context,
)


# Example 1: Basic decorator usage
@ai_usage_tracked
def analyze_receipt_text(text: str) -> Dict[str, Any]:
    """Simple function with automatic tracking."""
    print(f"Analyzing text: {text[:50]}...")

    # Simulate AI processing
    # In real code, this would call OpenAI/Anthropic
    result = {
        "merchant": "Demo Store",
        "total": 42.99,
        "date": "2024-01-15",
    }

    # Check current context
    context = get_current_context()
    print(f"Current operation: {context['operation_type']}")

    return result


# Example 2: Decorator with custom metadata
@ai_usage_tracked(
    operation_type="receipt_batch_processing",
    priority="high",
    source="mobile_app",
)
def process_receipt_batch(receipts: List[str]) -> List[Dict[str, Any]]:
    """Process multiple receipts with custom tracking metadata."""
    print(f"Processing batch of {len(receipts)} receipts")

    results = []
    for i, receipt in enumerate(receipts):
        # Each receipt is processed with parent context
        result = analyze_receipt_text(receipt)
        results.append(result)

    return results


# Example 3: Function that accepts tracker
@ai_usage_tracked(operation_type="merchant_validation")
def validate_merchant(merchant_name: str, tracker=None) -> bool:
    """Function that uses the tracker directly."""
    print(f"Validating merchant: {merchant_name}")

    # Tracker is automatically injected by decorator
    if tracker:
        # Add custom metadata during execution
        tracker.add_context_metadata(
            {
                "merchant_name": merchant_name,
                "validation_method": "google_places_api",
            }
        )

    # Simulate validation
    return len(merchant_name) > 3


# Example 4: Async function with decorator
@ai_usage_tracked(operation_type="async_analysis")
async def analyze_receipt_async(image_url: str) -> Dict[str, Any]:
    """Async function with automatic tracking."""
    print(f"Async processing image: {image_url}")

    # Simulate async processing
    await asyncio.sleep(0.1)

    return {"merchant": "Async Store", "total": 99.99, "confidence": 0.95}


# Example 5: Context manager for explicit control
async def process_with_context_manager():
    """Demonstrate explicit context manager usage."""

    # Basic context manager
    with ai_usage_context("manual_operation", request_id="req-123") as tracker:
        print("Inside context manager")

        # Simulate some work
        result = {"status": "processed"}

        # Add metadata during operation
        tracker.add_context_metadata(
            {"items_processed": 5, "processing_time_ms": 150}
        )

    # Batch context manager with automatic batch pricing
    with batch_ai_usage_context("batch-789", item_count=100) as tracker:
        print("Processing batch with special pricing")

        # Batch operations get 50% discount on pricing
        for i in range(5):
            # Each operation inherits batch context
            print(f"  Processing item {i}")


# Example 6: Nested operations with context propagation
@ai_usage_tracked(operation_type="order_processing")
def process_order(order_id: str, job_id: str = None):
    """Demonstrate context propagation through nested calls."""
    print(f"Processing order {order_id}")

    # Context includes job_id if provided
    items = ["receipt1", "receipt2", "receipt3"]

    # This will have parent context
    results = process_receipt_batch(items)

    # Validate merchant from first result
    if results:
        validate_merchant(results[0]["merchant"])

    return {"order_id": order_id, "receipts_processed": len(results)}


# Example 7: Error handling
@ai_usage_tracked
def failing_operation():
    """Demonstrate that tracking works even with exceptions."""
    print("Starting operation that will fail...")

    # Add some context
    context = get_current_context()
    print(f"Thread ID: {context['thread_id']}")

    # This will fail but tracking will still be flushed
    raise ValueError("Simulated error")


def main():
    """Run all examples."""
    print("=== AI Usage Context Manager Demo ===\n")

    # Example 1: Basic decorator
    print("1. Basic decorator usage:")
    result = analyze_receipt_text("Receipt from Demo Store for $42.99")
    print(f"   Result: {result}\n")

    # Example 2: Decorator with metadata
    print("2. Batch processing with metadata:")
    receipts = ["Receipt 1", "Receipt 2", "Receipt 3"]
    batch_results = process_receipt_batch(receipts)
    print(f"   Processed {len(batch_results)} receipts\n")

    # Example 3: Function with tracker
    print("3. Function that uses tracker:")
    is_valid = validate_merchant("Demo Store LLC")
    print(f"   Merchant valid: {is_valid}\n")

    # Example 4: Async function
    print("4. Async function:")
    async_result = asyncio.run(
        analyze_receipt_async("https://example.com/receipt.jpg")
    )
    print(f"   Async result: {async_result}\n")

    # Example 5: Context managers
    print("5. Context manager usage:")
    asyncio.run(process_with_context_manager())
    print()

    # Example 6: Nested operations
    print("6. Nested operations with context:")
    order_result = process_order("order-123", job_id="job-456")
    print(f"   Order result: {order_result}\n")

    # Example 7: Error handling
    print("7. Error handling:")
    try:
        failing_operation()
    except ValueError as e:
        print(f"   Caught expected error: {e}")
        print("   (Metrics were still flushed)\n")

    # Example 8: Partial failure context
    print("8. Partial failure handling:")
    receipts_to_process = [
        {"id": "r1", "valid": True},
        {"id": "r2", "valid": False},
        {"id": "r3", "valid": True},
        {"id": "r4", "valid": False},
        {"id": "r5", "valid": True},
    ]

    with partial_failure_context(
        "batch_receipt_processing",
        batch_id="batch-789",
        continue_on_error=True,
    ) as ctx:
        for receipt in receipts_to_process:
            try:
                if not receipt["valid"]:
                    raise ValueError(f"Invalid receipt {receipt['id']}")
                print(f"   Processed {receipt['id']}")
                ctx["success_count"] += 1
            except Exception as e:
                print(f"   Failed {receipt['id']}: {e}")
                ctx["errors"].append(
                    {"receipt_id": receipt["id"], "error": str(e)}
                )
                ctx["failure_count"] += 1

    print(
        f"   Summary: {ctx['success_count']} succeeded, {ctx['failure_count']} failed\n"
    )

    print("=== Demo Complete ===")


if __name__ == "__main__":
    main()
