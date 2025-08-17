"""
Simplified Implementation - Actually uses LangChain!
====================================================

This is a cleaner version that properly uses LangChain + Ollama
instead of bypassing it with raw HTTP calls.
"""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os

from .graph_design import (
    create_validation_graph,
    validate_receipt_labels as graph_validate,
    test_ollama_connection,
)


@dataclass
class SimpleConfig:
    """Simple configuration for the validator"""

    cache_enabled: bool = True
    batch_size: int = 10
    max_concurrent: int = 5


class SimpleReceiptValidator:
    """
    Simplified validator that actually uses LangChain
    """

    def __init__(self, config: Optional[SimpleConfig] = None):
        """Initialize the validator"""
        self.config = config or SimpleConfig()
        self.cache: Dict[str, Any] = {}
        self.graph = create_validation_graph()

    async def validate_single(
        self, image_id: str, receipt_id: int, labels: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Validate a single receipt's labels

        Args:
            image_id: Receipt image ID
            receipt_id: Receipt ID in database
            labels: Labels to validate

        Returns:
            Validation results
        """
        # Check cache
        cache_key = f"{image_id}:{receipt_id}"
        if self.config.cache_enabled and cache_key in self.cache:
            return self.cache[cache_key]

        # Use the graph to validate
        result = await graph_validate(image_id, receipt_id, labels)

        # Cache if successful
        if self.config.cache_enabled and result["success"]:
            self.cache[cache_key] = result

        return result

    async def validate_batch(
        self, receipts: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Validate multiple receipts concurrently

        Args:
            receipts: List of receipts, each with:
                - image_id, receipt_id, labels

        Returns:
            List of validation results
        """
        # Create validation tasks
        tasks = []
        for receipt in receipts:
            task = self.validate_single(
                receipt["image_id"], receipt["receipt_id"], receipt["labels"]
            )
            tasks.append(task)

        # Run concurrently with max limit
        results = []
        for i in range(0, len(tasks), self.config.max_concurrent):
            batch = tasks[i : i + self.config.max_concurrent]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

        return results


# ============================================================================
# Example Usage
# ============================================================================


async def demo():
    """Demo showing how to use the validator"""

    print("=" * 60)
    print("SIMPLE LANGCHAIN + OLLAMA VALIDATION DEMO")
    print("=" * 60)

    # Test Ollama connection first
    print("\n1. Testing Ollama connection...")
    if not await test_ollama_connection():
        print("   Please make sure Ollama is running!")
        return

    print("\n2. Creating validator...")
    validator = SimpleReceiptValidator()

    print("\n3. Validating sample labels...")

    # Sample data
    sample_labels = [
        {
            "image_id": "TEST_001",
            "receipt_id": 12345,
            "line_id": 1,
            "word_id": 1,
            "label": "MERCHANT_NAME",
            "validation_status": "NONE",
        },
        {
            "image_id": "TEST_001",
            "receipt_id": 12345,
            "line_id": 5,
            "word_id": 2,
            "label": "TOTAL",
            "validation_status": "NONE",
        },
    ]

    try:
        # Single validation
        result = await validator.validate_single(
            "TEST_001", 12345, sample_labels
        )

        print(f"\n✅ Validation complete!")
        print(f"   Success: {result['success']}")
        print(f"   Results: {result['validation_results']}")

        if result.get("error"):
            print(f"   Error: {result['error']}")

    except Exception as e:
        print(f"\n❌ Validation failed: {e}")

    print("\n" + "=" * 60)


# ============================================================================
# Quick Start Guide
# ============================================================================


def print_setup_guide():
    """Print setup instructions"""
    print(
        """
QUICK START GUIDE
=================

1. For LOCAL Ollama:
   - Install Ollama: https://ollama.ai
   - Pull a model: ollama pull llama3.1:8b
   - Run Ollama: ollama serve
   - Set environment:
     export OLLAMA_BASE_URL="http://localhost:11434"
     export OLLAMA_MODEL="llama3.1:8b"

2. For OLLAMA TURBO (your subscription):
   - Set environment:
     export OLLAMA_BASE_URL="https://api.ollama.com"
     export OLLAMA_API_KEY="your-api-key"
     export OLLAMA_MODEL="turbo"

3. Run validation:
   python -m receipt_label.langchain_validation.implementation_fixed

That's it! The system will use LangChain + Ollama to validate labels.
    """
    )


if __name__ == "__main__":
    # Check if Ollama is configured
    if not os.getenv("OLLAMA_BASE_URL"):
        print_setup_guide()
    else:
        # Run the demo
        asyncio.run(demo())
