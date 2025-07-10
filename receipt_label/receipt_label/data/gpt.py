"""
GPT integration functions for receipt labeling.

This module provides stub implementations of GPT-based analysis functions.
These functions return mock data and are intended for testing purposes only.
The production system uses modern batch processing approaches instead of direct GPT calls.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def gpt_request_line_item_analysis(
    receipt: Any,
    receipt_lines: List[Any],
    receipt_words: List[Any],
    traditional_analysis: Dict,
    places_api_data: Optional[Dict],
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Analyze line items in a receipt using GPT.

    This is a stub implementation that returns mock data for testing.
    """
    logger.warning(
        "Using stub implementation of gpt_request_line_item_analysis"
    )

    # Return mock line item analysis
    mock_response = {
        "items": [],
        "subtotal": None,
        "tax": None,
        "total": None,
        "reasoning": "Stub implementation - no actual analysis performed",
    }

    return (mock_response, "mock_query", "mock_raw_response")


def gpt_request_spatial_currency_analysis(
    receipt_lines: List[Any],
    receipt_words: List[Any],
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Analyze currency amounts with spatial context using GPT.

    This is a stub implementation that returns mock data for testing.
    """
    logger.warning(
        "Using stub implementation of gpt_request_spatial_currency_analysis"
    )

    # Return mock spatial currency analysis
    mock_response = {
        "currency_amounts": [],
        "spatial_patterns": [],
        "reasoning": "Stub implementation - no actual analysis performed",
    }

    return (mock_response, "mock_query", "mock_raw_response")


def gpt_request_structure_analysis(
    receipt: Any,
    receipt_lines: List[Any],
    receipt_words: List[Any],
    places_api_data: Optional[Dict],
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Analyze receipt structure using GPT.

    This is a stub implementation that returns mock data for testing.
    """
    logger.warning(
        "Using stub implementation of gpt_request_structure_analysis"
    )

    # Return mock structure analysis
    mock_response = {
        "discovered_sections": [],
        "overall_confidence": 0.5,
        "reasoning": "Stub implementation - no actual analysis performed",
    }

    return (mock_response, "mock_query", "mock_raw_response")


def gpt_request_field_labeling(
    receipt: Any,
    receipt_lines: List[Any],
    receipt_words: List[Any],
    section_boundaries: Any,
    places_api_data: Optional[Dict],
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Label individual fields in a receipt using GPT.

    This is a stub implementation that returns mock data for testing.
    """
    logger.warning("Using stub implementation of gpt_request_field_labeling")

    # Return mock field labeling analysis
    mock_response = {
        "labels": [],
        "metadata": {
            "total_labeled_words": 0,
            "average_confidence": 0.5,
            "requires_review": False,
            "review_reasons": [],
        },
        "reasoning": "Stub implementation - no actual analysis performed",
    }

    return (mock_response, "mock_query", "mock_raw_response")