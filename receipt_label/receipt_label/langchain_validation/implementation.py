"""
Optimized Implementation - Minimal LLM Usage
============================================

This implementation uses the optimized graph design that prepares
context outside the graph to minimize Ollama API calls.
"""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os

from .graph_design import (
    prepare_validation_context,
    create_minimal_validation_graph,
    update_database_with_results,
    validate_receipt_labels_optimized,
    CachedValidator,
    get_ollama_llm,
    MinimalValidationState,
)


@dataclass
class SimpleConfig:
    """Simple configuration for the validator"""

    cache_enabled: bool = True
    context_cache_ttl: int = 300  # 5 minutes
    batch_size: int = 10
    max_concurrent: int = 5


class OptimizedReceiptValidator:
    """
    Optimized validator that minimizes LLM usage
    """

    def __init__(self, config: Optional[SimpleConfig] = None):
        """Initialize the validator"""
        self.config = config or SimpleConfig()
        self.cached_validator = (
            CachedValidator(cache_ttl_seconds=self.config.context_cache_ttl)
            if self.config.cache_enabled
            else None
        )
        self.graph = create_minimal_validation_graph()

    async def validate_single(
        self,
        image_id: str,
        receipt_id: int,
        labels: List[Dict[str, Any]],
        skip_database_update: bool = False,
    ) -> Dict[str, Any]:
        """
        Validate a single receipt's labels with optimization

        Args:
            image_id: Receipt image ID
            receipt_id: Receipt ID in database
            labels: Labels to validate
            skip_database_update: If True, don't update database

        Returns:
            Validation results with metadata
        """
        # Use cached validator if enabled
        if self.config.cache_enabled and self.cached_validator:
            result = await self.cached_validator.validate_with_cache(
                image_id, receipt_id, labels
            )

            # Update database if needed
            if not skip_database_update and result.get("success"):
                context = prepare_validation_context(
                    image_id, receipt_id, labels
                )
                db_result = update_database_with_results(
                    context, result.get("validation_results", [])
                )
                result["database_result"] = db_result
                result["database_updated"] = True

            return result

        # Use regular optimized validation
        return await validate_receipt_labels_optimized(
            image_id, receipt_id, labels, skip_database_update
        )

    async def validate_batch(
        self,
        receipts: List[Dict[str, Any]],
        skip_database_update: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Validate multiple receipts with concurrency control

        Args:
            receipts: List of receipts, each with:
                - image_id, receipt_id, labels
            skip_database_update: If True, don't update database

        Returns:
            List of validation results
        """
        # Create validation tasks
        tasks = []
        for receipt in receipts:
            task = self.validate_single(
                receipt["image_id"],
                receipt["receipt_id"],
                receipt["labels"],
                skip_database_update,
            )
            tasks.append(task)

        # Run concurrently with max limit
        results = []
        for i in range(0, len(tasks), self.config.max_concurrent):
            batch = tasks[i : i + self.config.max_concurrent]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

            # Small delay between batches to prevent rate limiting
            if i + self.config.max_concurrent < len(tasks):
                await asyncio.sleep(0.5)

        return results

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        if self.cached_validator:
            return {
                "cache_enabled": True,
                "cached_contexts": len(self.cached_validator.context_cache),
                "cache_ttl_seconds": self.cached_validator.cache_ttl,
            }
        return {"cache_enabled": False}


# ============================================================================
# Convenience Functions
# ============================================================================


async def validate_receipt_labels(
    image_id: str,
    receipt_id: int,
    labels: List[Dict[str, Any]],
    use_cache: bool = True,
    skip_database_update: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function for validating receipt labels

    Args:
        image_id: Receipt image ID
        receipt_id: Receipt ID
        labels: Labels to validate
        use_cache: Whether to use context caching
        skip_database_update: If True, don't update database

    Returns:
        Validation results
    """
    config = SimpleConfig(cache_enabled=use_cache)
    validator = OptimizedReceiptValidator(config)
    return await validator.validate_single(
        image_id, receipt_id, labels, skip_database_update
    )


async def test_ollama_connection():
    """Test if Ollama is working with LangChain"""
    try:
        from langchain_core.messages import HumanMessage

        llm = get_ollama_llm()
        messages = [
            HumanMessage(content="Say 'Ollama is working!' in JSON format")
        ]
        response = await llm.ainvoke(messages)
        print(f"✅ Ollama test successful: {response.content}")
        return True
    except Exception as e:
        print(f"❌ Ollama test failed: {e}")
        return False


# ============================================================================
# Demo and Testing
# ============================================================================


async def demo():
    """Demo showing the optimized validation"""

    print("=" * 60)
    print("OPTIMIZED LANGCHAIN VALIDATION DEMO")
    print("=" * 60)

    # Test Ollama connection first
    print("\n1. Testing Ollama connection...")
    if not await test_ollama_connection():
        print("   Please make sure Ollama is running!")
        return

    print("\n2. Creating optimized validator...")
    validator = OptimizedReceiptValidator()

    print("\n3. Preparing sample data...")

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
        print("\n4. First validation (fetches context)...")
        result1 = await validator.validate_single(
            "TEST_001", 12345, sample_labels, skip_database_update=True
        )

        print(f"   Success: {result1['success']}")
        print(f"   LLM calls: {result1.get('llm_calls', 0)}")
        print(f"   Used cache: {result1.get('used_cache', False)}")

        print("\n5. Second validation (uses cached context)...")
        result2 = await validator.validate_single(
            "TEST_001", 12345, sample_labels, skip_database_update=True
        )

        print(f"   Success: {result2['success']}")
        print(f"   LLM calls: {result2.get('llm_calls', 0)}")
        print(f"   Used cache: {result2.get('used_cache', True)}")

        print(f"\n6. Cache statistics:")
        stats = validator.get_cache_stats()
        for key, value in stats.items():
            print(f"   {key}: {value}")

    except Exception as e:
        print(f"\n❌ Validation failed: {e}")

    print("\n" + "=" * 60)
    print("The optimized design minimizes LLM usage by:")
    print("1. Preparing context outside the graph")
    print("2. Caching prepared contexts")
    print("3. Only calling LLM when necessary")


def print_setup_guide():
    """Print setup instructions"""
    print(
        """
OPTIMIZED VALIDATION SETUP
==========================

1. For LOCAL Ollama:
   export OLLAMA_BASE_URL="http://localhost:11434"
   export OLLAMA_MODEL="llama3.1:8b"

2. For OLLAMA TURBO:
   export OLLAMA_BASE_URL="https://api.ollama.com"
   export OLLAMA_API_KEY="your-api-key"
   export OLLAMA_MODEL="turbo"

3. Run validation:
   from receipt_label.langchain_validation import validate_receipt_labels
   
   result = await validate_receipt_labels(
       image_id="IMG_001",
       receipt_id=12345,
       labels=[...],
       use_cache=True,  # Enable context caching
       skip_database_update=False  # Update database
   )

Benefits:
- Context is prepared once and cached
- LLM is only called when needed
- Database updates are decoupled from validation
    """
    )


if __name__ == "__main__":
    # Check if Ollama is configured
    if not os.getenv("OLLAMA_BASE_URL"):
        print_setup_guide()
    else:
        # Run the demo
        asyncio.run(demo())
