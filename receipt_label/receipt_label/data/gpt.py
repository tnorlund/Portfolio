"""
GPT integration functions for receipt labeling.

This module provides GPT-based analysis functions for receipt processing.
These are temporary stubs until the functions are properly implemented in receipt_dynamo.
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
    Forward to the actual implementation in receipt_dynamo.
    """
    from receipt_dynamo.data._gpt import (
        gpt_request_structure_analysis as _gpt_request_structure_analysis,
    )

    return _gpt_request_structure_analysis(
        receipt, receipt_lines, receipt_words, places_api_data, gpt_api_key
    )


def gpt_request_field_labeling(
    receipt: Any,
    receipt_lines: List[Any],
    receipt_words: List[Any],
    section_boundaries: Any,
    places_api_data: Optional[Dict],
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Forward to the actual implementation in receipt_dynamo.
    """
    from receipt_dynamo.data._gpt import (
        gpt_request_field_labeling as _gpt_request_field_labeling,
    )

    return _gpt_request_field_labeling(
        receipt,
        receipt_lines,
        receipt_words,
        section_boundaries,
        places_api_data,
        gpt_api_key,
    )
