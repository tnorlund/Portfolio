"""
DEPRECATED: CoVe (Consistency Verification) workflow for checking receipt text consistency.

This module is deprecated. The implementation has been moved to:
    receipt_agent.subagents.cove_text_consistency

This file is kept for backward compatibility and will be removed in a future version.
Please update imports to use receipt_agent.subagents.cove_text_consistency instead.

This sub-agent verifies that all receipts sharing the same place_id actually
contain text consistent with being from the same place. It compares receipt
text against canonical metadata to identify outliers.
"""

# Re-export from new location
from receipt_agent.subagents.cove_text_consistency import (
    CoveTextConsistencyState,
    create_cove_text_consistency_graph,
    run_cove_text_consistency,
)

__all__ = [
    "CoveTextConsistencyState",
    "create_cove_text_consistency_graph",
    "run_cove_text_consistency",
]
