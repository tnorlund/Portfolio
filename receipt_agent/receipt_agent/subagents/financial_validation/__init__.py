"""
Financial Validation Sub-Agent

Validates financial consistency on receipts:
- Validates grand total = subtotal + tax + fees - discounts
- Validates subtotal = sum of line totals
- Validates line items: quantity × unit_price ≈ line_total
- Detects currency and ensures consistency
- Identifies which labels need correction
"""

from receipt_agent.subagents.financial_validation.graph import (
    create_financial_validation_graph,
    run_financial_validation,
)
from receipt_agent.subagents.financial_validation.state import (
    FinancialValidationState,
)

__all__ = [
    "FinancialValidationState",
    "create_financial_validation_graph",
    "run_financial_validation",
]


