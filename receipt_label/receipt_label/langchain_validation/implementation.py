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
)
from .models import ValidationResponse, ValidationResult


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


async def test_ollama_connection() -> bool:
    """Test if Ollama is working"""
    try:
        # Load environment variables from .env file
        from dotenv import load_dotenv
        load_dotenv()
        
        # Verify API key is loaded
        if not os.getenv('OLLAMA_API_KEY'):
            print("❌ OLLAMA_API_KEY not found in environment")
            return False
        llm = get_ollama_llm()

        # Simple connectivity test
        test_prompt = """Validate this word label:
        Word: 'August'
        Label: DATE
        
        Respond with JSON:
        {"id": "TEST#001", "is_valid": true}
        """

        response = await llm.ainvoke(test_prompt)

        if response and response.content:
            print(f"✅ Ollama connection successful")
            print(f"   Model: gpt-oss:120b")
            print(f"   Base URL: https://ollama.com")
            return True
        else:
            print(f"❌ No response from Ollama")
            return False

    except Exception as e:
        print(f"❌ Ollama connection failed: {e}")
        return False


# ============================================================================
# Demo and Testing
# ============================================================================


async def demo() -> None:
    """Demo showing the optimized validation"""
    
    # Load environment variables from .env file
    from dotenv import load_dotenv
    load_dotenv()

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

    # Get real receipt data for testing
    print("   Fetching sample receipt from database...")
    from receipt_label.utils import get_client_manager

    try:
        client_manager = get_client_manager()
        receipts_result = client_manager.dynamo.list_receipts(limit=1)
        receipts = (
            receipts_result[0]
            if isinstance(receipts_result, tuple)
            else receipts_result
        )

        if not receipts:
            print("   No receipts found in database, using mock data")
            sample_labels = [
                {
                    "image_id": "TEST_001",
                    "receipt_id": 12345,
                    "line_id": 1,
                    "word_id": 1,
                    "label": "MERCHANT_NAME",
                    "validation_status": "NONE",
                }
            ]
            image_id = "TEST_001"
            receipt_id = 12345
        else:
            receipt = receipts[0]
            image_id = receipt.image_id
            receipt_id = receipt.receipt_id

            # Create a test label from first word
            receipt_details = client_manager.dynamo.get_receipt_details(
                image_id, receipt_id
            )
            if receipt_details and receipt_details.words:
                first_word = receipt_details.words[0]
                sample_labels = [
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "line_id": first_word.line_id,
                        "word_id": first_word.word_id,
                        "label": "MERCHANT_NAME",
                        "validation_status": "NONE",
                    }
                ]
                print(f"   Using word '{first_word.text}' from real receipt")
            else:
                print("   No words found, using mock data")
                sample_labels = [
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "line_id": 1,
                        "word_id": 1,
                        "label": "MERCHANT_NAME",
                        "validation_status": "NONE",
                    }
                ]
    except Exception as e:
        print(f"   Database error: {e}, using mock data")
        sample_labels = [
            {
                "image_id": "TEST_001",
                "receipt_id": 12345,
                "line_id": 1,
                "word_id": 1,
                "label": "MERCHANT_NAME",
                "validation_status": "NONE",
            }
        ]
        image_id = "TEST_001"
        receipt_id = 12345

    try:
        print("\n4. First validation (fetches context)...")
        result1 = await validator.validate_single(
            image_id, receipt_id, sample_labels, skip_database_update=True
        )

        print(f"   Success: {result1['success']}")
        print(f"   LLM calls: {result1.get('llm_calls', 0)}")
        print(f"   Used cache: {result1.get('used_cache', False)}")

        print("\n5. Second validation (uses cached context if enabled)...")
        result2 = await validator.validate_single(
            image_id, receipt_id, sample_labels, skip_database_update=True
        )

        print(f"   Success: {result2['success']}")
        print(f"   LLM calls: {result2.get('llm_calls', 0)}")
        print(f"   Used cache: {result2.get('used_cache', False)}")

        # Show validation results
        if result2.get("validation_results"):
            print("\n   Validation Results:")
            for r in result2["validation_results"]:
                valid = "✅ VALID" if r.get("is_valid") else "❌ INVALID"
                print(f"     {valid}")
                if not r.get("is_valid") and r.get("correct_label"):
                    print(f"     Suggested: {r['correct_label']}")

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


def print_setup_guide() -> None:
    """Print setup instructions"""
    print(
        """
OPTIMIZED VALIDATION SETUP
==========================

1. Set your Ollama Turbo API key in .env file:
   OLLAMA_API_KEY=your-api-key-here

2. Run validation:
   from receipt_label.langchain_validation import validate_receipt_labels
   
   result = await validate_receipt_labels(
       image_id="IMG_001",
       receipt_id=12345,
       labels=[...],
       use_cache=True,  # Enable context caching
       skip_database_update=False  # Update database
   )

Benefits:
- Uses Ollama Turbo (gpt-oss:120b model) for fast processing
- Context is prepared once and cached  
- LLM is only called when needed
- Database updates are decoupled from validation
- Only requires API key - base URL and model are hardcoded
    """
    )


if __name__ == "__main__":
    # Load environment and check if Ollama API key is configured
    from dotenv import load_dotenv
    load_dotenv()
    
    if not os.getenv("OLLAMA_API_KEY"):
        print_setup_guide()
    else:
        # Run the demo
        asyncio.run(demo())
