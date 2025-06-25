"""
Cost calculator for AI services based on current pricing models.
Prices should be updated regularly as providers change their pricing.
"""

from datetime import datetime
from typing import Dict, Optional, Tuple


class AICostCalculator:
    """
    Calculate costs for various AI service providers.
    All prices are in USD per 1,000 tokens unless otherwise noted.

    Last updated: December 2024
    """

    # OpenAI Pricing (per 1k tokens)
    OPENAI_PRICING = {
        # GPT-3.5
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        "gpt-3.5-turbo-16k": {"input": 0.0005, "output": 0.0015},
        "gpt-3.5-turbo-1106": {"input": 0.0010, "output": 0.0020},
        "gpt-3.5-turbo-0125": {"input": 0.0005, "output": 0.0015},
        # GPT-4
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4-32k": {"input": 0.06, "output": 0.12},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-4-turbo-preview": {"input": 0.01, "output": 0.03},
        "gpt-4-1106-preview": {"input": 0.01, "output": 0.03},
        # GPT-4o
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        # Embeddings (per 1k tokens, input only)
        "text-embedding-3-small": {"input": 0.00002},
        "text-embedding-3-large": {"input": 0.00013},
        "text-embedding-ada-002": {"input": 0.0001},
        # Legacy models
        "text-davinci-003": {"input": 0.02, "output": 0.02},
    }

    # Anthropic Claude Pricing (per 1k tokens)
    ANTHROPIC_PRICING = {
        # Claude 3 Opus
        "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
        "claude-3-opus": {"input": 0.015, "output": 0.075},
        "claude-opus-4": {
            "input": 0.015,
            "output": 0.075,
        },  # Assuming same as 3-opus
        # Claude 3.5 Sonnet
        "claude-3.5-sonnet": {"input": 0.003, "output": 0.015},
        "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
        # Claude 3 Sonnet
        "claude-3-sonnet-20240229": {"input": 0.003, "output": 0.015},
        "claude-3-sonnet": {"input": 0.003, "output": 0.015},
        # Claude 3 Haiku
        "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
        "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
        # Claude 2
        "claude-2.1": {"input": 0.008, "output": 0.024},
        "claude-2.0": {"input": 0.008, "output": 0.024},
        # Claude Instant
        "claude-instant-1.2": {"input": 0.0008, "output": 0.0024},
    }

    # Google Places API Pricing (per request)
    GOOGLE_PLACES_PRICING = {
        "place_details": 0.017,  # $17 per 1,000 requests
        "place_search": 0.032,  # $32 per 1,000 requests
        "place_autocomplete": 0.00283,  # $2.83 per 1,000 requests
        "place_photo": 0.007,  # $7 per 1,000 requests
        "geocoding": 0.005,  # $5 per 1,000 requests
    }

    # OpenAI Batch API Discount
    OPENAI_BATCH_DISCOUNT = 0.5  # 50% discount for batch API

    @classmethod
    def calculate_openai_cost(
        cls,
        model: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        is_batch: bool = False,
    ) -> Optional[float]:
        """
        Calculate OpenAI API cost.

        Args:
            model: The model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            total_tokens: Total tokens (for embeddings)
            is_batch: Whether this is a batch API request (50% discount)

        Returns:
            Cost in USD or None if model not found
        """
        if model not in cls.OPENAI_PRICING:
            return None

        pricing = cls.OPENAI_PRICING[model]
        cost = 0.0

        # For embeddings (input only)
        if "embedding" in model and total_tokens:
            cost = (total_tokens / 1000) * pricing["input"]
        # For completions (input + output)
        elif input_tokens and output_tokens:
            cost = (input_tokens / 1000) * pricing["input"]
            cost += (output_tokens / 1000) * pricing["output"]
        # Fallback to total tokens
        elif total_tokens and "input" in pricing:
            # Assume 50/50 split if not specified
            if "output" in pricing:
                input_estimate = total_tokens // 2
                output_estimate = total_tokens - input_estimate
                cost = (input_estimate / 1000) * pricing["input"]
                cost += (output_estimate / 1000) * pricing["output"]
            else:
                cost = (total_tokens / 1000) * pricing["input"]

        # Apply batch discount if applicable
        if is_batch:
            cost *= cls.OPENAI_BATCH_DISCOUNT

        return round(cost, 6)  # Round to 6 decimal places

    @classmethod
    def calculate_anthropic_cost(
        cls,
        model: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> Optional[float]:
        """
        Calculate Anthropic Claude API cost.

        Args:
            model: The model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            total_tokens: Total tokens (fallback)

        Returns:
            Cost in USD or None if model not found
        """
        # Try exact match first
        pricing = cls.ANTHROPIC_PRICING.get(model)

        # If not found, try to match partial model names
        if not pricing:
            for key, value in cls.ANTHROPIC_PRICING.items():
                if key in model or model in key:
                    pricing = value
                    break

        if not pricing:
            return None

        cost = 0.0

        if input_tokens and output_tokens:
            cost = (input_tokens / 1000) * pricing["input"]
            cost += (output_tokens / 1000) * pricing["output"]
        elif total_tokens:
            # Assume 30/70 split (input/output) for Claude
            input_estimate = int(total_tokens * 0.3)
            output_estimate = total_tokens - input_estimate
            cost = (input_estimate / 1000) * pricing["input"]
            cost += (output_estimate / 1000) * pricing["output"]

        return round(cost, 6)

    @classmethod
    def calculate_google_places_cost(
        cls, operation: str, api_calls: int = 1
    ) -> Optional[float]:
        """
        Calculate Google Places API cost.

        Args:
            operation: The API operation type
            api_calls: Number of API calls made

        Returns:
            Cost in USD or None if operation not found
        """
        if operation not in cls.GOOGLE_PLACES_PRICING:
            return None

        cost_per_call = cls.GOOGLE_PLACES_PRICING[operation]
        return round(cost_per_call * api_calls, 6)

    @classmethod
    def estimate_tokens_from_text(cls, text: str) -> int:
        """
        Rough estimation of tokens from text.
        More accurate counting requires tiktoken library.

        Rule of thumb: ~4 characters per token for English text
        """
        return len(text) // 4

    @classmethod
    def get_model_context_window(cls, model: str) -> Optional[int]:
        """Get the context window size for a model."""
        context_windows = {
            # OpenAI
            "gpt-3.5-turbo": 4096,
            "gpt-3.5-turbo-16k": 16384,
            "gpt-4": 8192,
            "gpt-4-32k": 32768,
            "gpt-4-turbo": 128000,
            "gpt-4o": 128000,
            # Anthropic
            "claude-3-opus": 200000,
            "claude-3.5-sonnet": 200000,
            "claude-3-sonnet": 200000,
            "claude-3-haiku": 200000,
            "claude-2.1": 200000,
            "claude-2.0": 100000,
            "claude-instant-1.2": 100000,
        }

        # Check exact match
        if model in context_windows:
            return context_windows[model]

        # Check partial match
        for key, value in context_windows.items():
            if key in model or model in key:
                return value

        return None

    @classmethod
    def get_pricing_info(cls, service: str) -> Dict:
        """Get all pricing info for a service."""
        if service.lower() == "openai":
            return cls.OPENAI_PRICING
        elif service.lower() == "anthropic":
            return cls.ANTHROPIC_PRICING
        elif service.lower() == "google_places":
            return cls.GOOGLE_PLACES_PRICING
        else:
            return {}

    @classmethod
    def format_cost(cls, cost: float) -> str:
        """Format cost for display."""
        if cost < 0.01:
            return f"${cost:.6f}"
        elif cost < 1:
            return f"${cost:.4f}"
        else:
            return f"${cost:.2f}"
