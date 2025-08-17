"""
LangChain Receipt Validation Implementation
==========================================

This module provides a practical implementation of the LangChain validation system
for receipt processing. It demonstrates how to use the designed graph with
Ollama models (local or Turbo), with proper environment configuration.

Usage:
    # Real-time validation
    validator = ReceiptValidator()
    result = await validator.validate_receipt_labels(image_id, receipt_id, labels)

    # Batch validation with cost optimization
    results = await validator.validate_batch(receipt_list)
"""

import asyncio
import json
import os
import time
from typing import List, Dict, Optional, Union, Any
from dataclasses import dataclass
from enum import Enum

from langchain_ollama import ChatOllama
from langchain_core.language_models.base import BaseLanguageModel
from langsmith import Client

from .graph_design import (
    create_validation_graph,
    validate_receipt_labels,
    ValidationState,
)


class LLMProvider(Enum):
    """Supported LLM providers"""

    OLLAMA = "ollama"


@dataclass
class ValidationConfig:
    """Configuration for receipt validation"""

    llm_provider: LLMProvider = LLMProvider.OLLAMA
    model_name: str = "llama3.1:8b"
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    timeout: int = 30

    # Ollama specific
    ollama_base_url: str = "http://localhost:11434"

    # Cost optimization
    enable_smart_batching: bool = True
    batch_threshold: int = 10
    cache_results: bool = True

    # Monitoring
    enable_langsmith: bool = True
    langsmith_project: str = "receipt-validation"

    @classmethod
    def from_env(cls) -> "ValidationConfig":
        """Load configuration from environment variables"""
        provider = LLMProvider.OLLAMA  # Only Ollama supported

        # Model selection for Ollama
        model_name = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

        return cls(
            llm_provider=provider,
            model_name=model_name,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
            max_tokens=(
                int(os.getenv("LLM_MAX_TOKENS", "1000"))
                if os.getenv("LLM_MAX_TOKENS")
                else None
            ),
            timeout=int(os.getenv("LLM_TIMEOUT", "30")),
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            ),
            enable_smart_batching=os.getenv(
                "ENABLE_SMART_BATCHING", "true"
            ).lower()
            == "true",
            batch_threshold=int(os.getenv("BATCH_THRESHOLD", "10")),
            cache_results=os.getenv("CACHE_RESULTS", "true").lower() == "true",
            enable_langsmith=os.getenv("LANGCHAIN_TRACING_V2", "false").lower()
            == "true",
            langsmith_project=os.getenv(
                "LANGCHAIN_PROJECT", "receipt-validation"
            ),
        )


class LLMFactory:
    """Factory for creating LLM instances"""

    @staticmethod
    def create_llm(config: ValidationConfig) -> BaseLanguageModel:
        """Create an LLM instance based on configuration"""

        base_kwargs = {
            "temperature": config.temperature,
            "timeout": config.timeout,
        }

        if config.max_tokens:
            base_kwargs["max_tokens"] = config.max_tokens

        if config.llm_provider == LLMProvider.OLLAMA:
            return ChatOllama(
                model=config.model_name,
                base_url=config.ollama_base_url,
                **base_kwargs,
            )

        else:
            raise ValueError(
                f"Unsupported LLM provider: {config.llm_provider}"
            )


class ValidationMetrics:
    """Tracks validation performance metrics"""

    def __init__(self):
        self.validation_count = 0
        self.total_time = 0.0
        self.success_count = 0
        self.error_count = 0
        self.token_usage = 0
        self.cost_estimate = 0.0

    def record_validation(
        self,
        duration: float,
        success: bool,
        tokens: int = 0,
        cost: float = 0.0,
    ):
        """Record validation metrics"""
        self.validation_count += 1
        self.total_time += duration
        self.token_usage += tokens
        self.cost_estimate += cost

        if success:
            self.success_count += 1
        else:
            self.error_count += 1

    @property
    def average_time(self) -> float:
        """Calculate average validation time"""
        return (
            self.total_time / self.validation_count
            if self.validation_count > 0
            else 0.0
        )

    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        return (
            self.success_count / self.validation_count
            if self.validation_count > 0
            else 0.0
        )

    def summary(self) -> Dict[str, Any]:
        """Get metrics summary"""
        return {
            "validation_count": self.validation_count,
            "average_time_seconds": self.average_time,
            "success_rate": self.success_rate,
            "total_tokens": self.token_usage,
            "estimated_cost_usd": self.cost_estimate,
            "errors": self.error_count,
        }


class ReceiptValidator:
    """Main receipt validation class"""

    def __init__(self, config: Optional[ValidationConfig] = None):
        """Initialize the validator with configuration"""
        self.config = config or ValidationConfig.from_env()
        self.llm = LLMFactory.create_llm(self.config)
        self.metrics = ValidationMetrics()
        self.cache: Dict[str, Any] = {}

        # Initialize LangSmith client if enabled
        self.langsmith_client = None
        if self.config.enable_langsmith:
            try:
                self.langsmith_client = Client()
            except Exception as e:
                print(f"Warning: Could not initialize LangSmith: {e}")

    async def validate_single_receipt(
        self,
        image_id: str,
        receipt_id: int,
        labels: List[Dict[str, Any]],
        tracking_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Validate labels for a single receipt

        Args:
            image_id: Receipt image identifier
            receipt_id: Receipt ID in database
            labels: List of labels to validate
            tracking_context: Optional context for tracking/logging

        Returns:
            Validation results with metadata
        """
        start_time = time.time()

        # Create cache key for result caching
        cache_key = f"{image_id}:{receipt_id}:{hash(str(sorted(labels)))}"

        # Check cache if enabled
        if self.config.cache_results and cache_key in self.cache:
            cached_result = self.cache[cache_key]
            cached_result["from_cache"] = True
            return cached_result

        try:
            # Set up LangSmith tracing if enabled
            trace_context = {}
            if self.langsmith_client:
                trace_context = {
                    "project_name": self.config.langsmith_project,
                    "metadata": {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "label_count": len(labels),
                        "llm_provider": self.config.llm_provider.value,
                        "model": self.config.model_name,
                        **(tracking_context or {}),
                    },
                }

            # Create modified validation graph with our LLM
            validation_graph = self._create_custom_graph()

            # Run validation
            result = await validation_graph.ainvoke(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "labels_to_validate": labels,
                    "completed": False,
                }
            )

            # Extract metrics
            duration = time.time() - start_time
            success = result.get("completed", False)

            # Estimate token usage and cost (simplified)
            token_estimate = self._estimate_tokens(labels, result)
            cost_estimate = self._estimate_cost(token_estimate)

            # Record metrics
            self.metrics.record_validation(
                duration, success, token_estimate, cost_estimate
            )

            # Prepare final result
            final_result = {
                "success": success,
                "validation_results": result.get("validation_results", []),
                "error": result.get("error"),
                "metadata": {
                    "duration_seconds": duration,
                    "token_estimate": token_estimate,
                    "cost_estimate": cost_estimate,
                    "llm_provider": self.config.llm_provider.value,
                    "model": self.config.model_name,
                    "from_cache": False,
                },
            }

            # Cache successful results
            if self.config.cache_results and success:
                self.cache[cache_key] = final_result.copy()

            return final_result

        except Exception as e:
            duration = time.time() - start_time
            self.metrics.record_validation(duration, False)

            return {
                "success": False,
                "validation_results": [],
                "error": str(e),
                "metadata": {
                    "duration_seconds": duration,
                    "llm_provider": self.config.llm_provider.value,
                    "model": self.config.model_name,
                },
            }

    async def validate_batch(
        self, receipts: List[Dict[str, Any]], max_concurrent: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Validate multiple receipts with smart batching

        Args:
            receipts: List of receipts with format:
                [{"image_id": str, "receipt_id": int, "labels": List[Dict]}]
            max_concurrent: Maximum concurrent validations

        Returns:
            List of validation results
        """
        if self.config.enable_smart_batching:
            return await self._smart_batch_validate(receipts, max_concurrent)
        else:
            return await self._concurrent_validate(receipts, max_concurrent)

    async def _smart_batch_validate(
        self, receipts: List[Dict[str, Any]], max_concurrent: int
    ) -> List[Dict[str, Any]]:
        """Smart batching with cost optimization"""
        results = []

        # Group receipts by priority (urgent vs. normal)
        urgent_receipts = [r for r in receipts if r.get("urgent", False)]
        normal_receipts = [r for r in receipts if not r.get("urgent", False)]

        # Process urgent receipts immediately
        if urgent_receipts:
            urgent_results = await self._concurrent_validate(
                urgent_receipts, max_concurrent
            )
            results.extend(urgent_results)

        # Batch normal receipts for cost optimization
        if normal_receipts:
            batch_size = self.config.batch_threshold
            for i in range(0, len(normal_receipts), batch_size):
                batch = normal_receipts[i : i + batch_size]
                batch_results = await self._concurrent_validate(
                    batch, max_concurrent
                )
                results.extend(batch_results)

                # Small delay between batches to prevent rate limiting
                if i + batch_size < len(normal_receipts):
                    await asyncio.sleep(1)

        return results

    async def _concurrent_validate(
        self, receipts: List[Dict[str, Any]], max_concurrent: int
    ) -> List[Dict[str, Any]]:
        """Process receipts concurrently"""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def validate_with_semaphore(receipt):
            async with semaphore:
                return await self.validate_single_receipt(
                    receipt["image_id"],
                    receipt["receipt_id"],
                    receipt["labels"],
                    receipt.get("tracking_context"),
                )

        tasks = [validate_with_semaphore(receipt) for receipt in receipts]
        return await asyncio.gather(*tasks)

    def _create_custom_graph(self):
        """Create validation graph with custom LLM"""
        # This would be modified to inject our LLM into the graph
        # For now, we'll use the standard graph
        return create_validation_graph()

    def _estimate_tokens(self, labels: List[Dict], result: Dict) -> int:
        """Estimate token usage for cost calculation"""
        # Simplified token estimation
        input_tokens = (
            len(str(labels)) // 4
        )  # Rough estimate: 4 chars per token
        output_tokens = len(str(result.get("validation_results", []))) // 4
        return input_tokens + output_tokens

    def _estimate_cost(self, tokens: int) -> float:
        """Estimate cost based on token usage and provider"""
        # Ollama is typically free for local usage
        # Ollama Turbo may have costs but they are not per-token based
        return 0.0

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get comprehensive metrics summary"""
        return {
            **self.metrics.summary(),
            "configuration": {
                "llm_provider": self.config.llm_provider.value,
                "model": self.config.model_name,
                "temperature": self.config.temperature,
                "smart_batching": self.config.enable_smart_batching,
                "caching": self.config.cache_results,
            },
            "cache_stats": {"cached_entries": len(self.cache)},
        }


# Convenience functions for backward compatibility
async def validate_receipt_labels_v2(
    image_id: str,
    receipt_id: int,
    labels: List[Dict[str, Any]],
    llm_provider: str = "ollama",
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for validating receipt labels

    Args:
        image_id: Receipt image identifier
        receipt_id: Receipt ID in database
        labels: List of labels to validate
        llm_provider: Always "ollama" (only supported provider)
        model_name: Optional model override

    Returns:
        Validation results
    """
    config = ValidationConfig.from_env()
    config.llm_provider = LLMProvider.OLLAMA  # Only Ollama supported

    if model_name:
        config.model_name = model_name

    validator = ReceiptValidator(config)
    return await validator.validate_single_receipt(
        image_id, receipt_id, labels
    )


# Example usage
if __name__ == "__main__":

    async def demo():
        """Demonstration of the validation system"""

        # Sample receipt data
        sample_labels = [
            {
                "image_id": "IMG_001",
                "receipt_id": 12345,
                "line_id": 1,
                "word_id": 1,
                "label": "MERCHANT_NAME",
                "validation_status": "NONE",
            },
            {
                "image_id": "IMG_001",
                "receipt_id": 12345,
                "line_id": 2,
                "word_id": 3,
                "label": "TOTAL",
                "validation_status": "NONE",
            },
        ]

        # Test with Ollama
        print("\nTesting with Ollama...")
        try:
            ollama_result = await validate_receipt_labels_v2(
                "IMG_001", 12345, sample_labels, llm_provider="ollama"
            )
            print(f"Ollama Result: {json.dumps(ollama_result, indent=2)}")
        except Exception as e:
            print(f"Ollama Error: {e}")

        # Test batch validation
        print("\nTesting batch validation...")
        try:
            validator = ReceiptValidator()

            batch_receipts = [
                {
                    "image_id": "IMG_001",
                    "receipt_id": 12345,
                    "labels": sample_labels,
                    "urgent": False,
                },
                {
                    "image_id": "IMG_002",
                    "receipt_id": 12346,
                    "labels": sample_labels,
                    "urgent": True,
                },
            ]

            batch_results = await validator.validate_batch(batch_receipts)
            print(f"Batch Results: {json.dumps(batch_results, indent=2)}")

            # Show metrics
            metrics = validator.get_metrics_summary()
            print(f"\nMetrics: {json.dumps(metrics, indent=2)}")

        except Exception as e:
            print(f"Batch validation error: {e}")

    # Run demo
    asyncio.run(demo())
