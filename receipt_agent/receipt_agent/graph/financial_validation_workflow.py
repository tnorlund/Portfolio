"""
DEPRECATED: Financial Validation Sub-Agent Workflow

This module is deprecated. The implementation has been moved to:
    receipt_agent.subagents.financial_validation

This file is kept for backward compatibility and will be removed in a future version.
Please update imports to use receipt_agent.subagents.financial_validation instead.

A sub-agent that validates financial consistency on receipts:
- Validates grand total = subtotal + tax + fees - discounts
- Validates subtotal = sum of line totals
- Validates line items: quantity × unit_price ≈ line_total
- Detects currency and ensures consistency
- Identifies which labels need correction

This is used as a sub-agent by the label harmonizer to reduce context size.
"""

# Re-export from new location
from receipt_agent.subagents.financial_validation import (
    FinancialValidationState,
    create_financial_validation_graph,
    run_financial_validation,
)

__all__ = [
    "FinancialValidationState",
    "create_financial_validation_graph",
    "run_financial_validation",
]
