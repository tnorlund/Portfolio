"""Simple logging utilities for noise word detection."""

import logging
from typing import List

from receipt_dynamo.entities import ReceiptWord

logger = logging.getLogger(__name__)


def log_noise_filtering_stats(
    words: List[ReceiptWord], context: str = ""
) -> None:
    """
    Log statistics about noise word filtering.

    Args:
        words: List of receipt words that were analyzed
        context: Additional context for the log message
    """
    if not words:
        return

    total_words = len(words)
    noise_words = sum(1 for w in words if getattr(w, "is_noise", False))
    meaningful_words = total_words - noise_words
    noise_percentage = (
        (noise_words / total_words * 100) if total_words > 0 else 0
    )

    # Estimate cost savings (1.5 tokens per word, $0.00002 per token)
    tokens_saved = noise_words * 1.5
    cost_saved = tokens_saved * 0.00002

    log_message = (
        f"Noise filtering stats{f' ({context})' if context else ''}: "
        f"Total={total_words}, Noise={noise_words} ({noise_percentage:.1f}%), "
        f"Meaningful={meaningful_words}, "
        f"Est. tokens saved={tokens_saved:.0f} (${cost_saved:.4f})"
    )

    logger.info(log_message)
