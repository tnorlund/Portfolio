"""
Domain models and constants shared across agents.

This module contains common data structures, enums, and constants used
by multiple agents and sub-agents.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

# Re-export from state.models for backward compatibility
from receipt_agent.state.models import (
    ChromaSearchResult,
    EvidenceType,
    MerchantCandidate,
    ReceiptContext,
    ToolCall,
    ValidationResult,
    ValidationStatus,
    VerificationEvidence,
    VerificationStep,
)

__all__ = [
    "ChromaSearchResult",
    "EvidenceType",
    "MerchantCandidate",
    "ReceiptContext",
    "ToolCall",
    "ValidationResult",
    "ValidationStatus",
    "VerificationEvidence",
    "VerificationStep",
]
