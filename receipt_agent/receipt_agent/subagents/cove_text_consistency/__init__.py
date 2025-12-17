"""
CoVe (Consistency Verification) Text Consistency Sub-Agent

Verifies that all receipts sharing the same place_id actually contain
text consistent with being from the same place. Compares receipt text
against canonical metadata to identify outliers.
"""

from receipt_agent.subagents.cove_text_consistency.graph import (
    create_cove_text_consistency_graph,
    run_cove_text_consistency,
)
from receipt_agent.subagents.cove_text_consistency.state import (
    CoveTextConsistencyState,
)

__all__ = [
    "CoveTextConsistencyState",
    "create_cove_text_consistency_graph",
    "run_cove_text_consistency",
]



