"""
DEPRECATED: Agentic workflow for finding complete receipt metadata.

This module is deprecated. The implementation has been moved to:
    receipt_agent.subagents.metadata_finder

This file is kept for backward compatibility and will be removed in a future version.
Please update imports to use receipt_agent.subagents.metadata_finder instead.

This workflow uses an LLM agent to find ALL missing metadata for a receipt:
- place_id (Google Place ID)
- merchant_name (business name)
- address (formatted address)
- phone_number (phone number)

The agent intelligently:
1. Examines receipt content (lines, words, labels)
2. Extracts metadata from receipt itself
3. Searches Google Places API for missing fields
4. Uses similar receipts for verification
5. Reasons about the best values for each field

Key Improvements Over Place ID Finder:
- Finds ALL missing metadata, not just place_id
- Can extract metadata from receipt content even if Google Places fails
- Handles partial metadata intelligently
- Better reasoning about what's missing and how to find it
"""

# Re-export from new location
from receipt_agent.subagents.metadata_finder import (
    ReceiptMetadataFinderState,
    create_receipt_metadata_finder_graph,
    run_receipt_metadata_finder,
)

__all__ = [
    "ReceiptMetadataFinderState",
    "create_receipt_metadata_finder_graph",
    "run_receipt_metadata_finder",
]
