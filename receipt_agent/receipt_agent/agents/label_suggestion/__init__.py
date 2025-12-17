"""
Label Suggestion Agent

Finds unlabeled words on a receipt and suggests labels using ChromaDB
similarity search, minimizing LLM calls.
"""

from receipt_agent.agents.label_suggestion.graph import (
    suggest_labels_for_receipt,
)

__all__ = ["suggest_labels_for_receipt"]



