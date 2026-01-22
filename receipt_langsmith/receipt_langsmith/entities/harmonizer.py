"""Harmonizer trace schemas.

This module defines schemas for parsing Harmonizer agent trace
inputs/outputs from LangSmith exports. This agent harmonizes metadata
across receipts sharing the same place_id.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from receipt_langsmith.entities.place_id_finder import ToolCallTrace


class ReceiptMetadataSummary(BaseModel):
    """Summary of a receipt's metadata for harmonization."""

    image_id: str
    """Receipt image ID."""

    receipt_id: int
    """Receipt ID within image."""

    merchant_name: Optional[str] = None
    """Current merchant name."""

    address: Optional[str] = None
    """Current address."""

    phone: Optional[str] = None
    """Current phone number."""


class HarmonizerInputs(BaseModel):
    """Inputs to Harmonizer trace."""

    place_id: str
    """Google Place ID to harmonize receipts for."""

    receipts: list[ReceiptMetadataSummary] = Field(default_factory=list)
    """Receipts to harmonize."""


class HarmonizedField(BaseModel):
    """A harmonized metadata field."""

    field_name: str
    """Name of the field (merchant_name, address, phone)."""

    harmonized_value: str
    """The harmonized value to apply."""

    source: str
    """Source of the harmonized value (consensus, google_places, etc.)."""

    confidence: float = 0.0
    """Confidence in the harmonized value (0-1)."""

    original_values: list[str] = Field(default_factory=list)
    """Original values from receipts before harmonization."""


class HarmonizerOutputs(BaseModel):
    """Outputs from Harmonizer trace."""

    harmonized_fields: list[HarmonizedField] = Field(default_factory=list)
    """Fields that were harmonized."""

    conflicts_resolved: int = 0
    """Number of conflicts resolved."""

    receipts_updated: int = 0
    """Number of receipts updated."""

    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    """Tool calls made during harmonization."""

    reasoning: str = ""
    """Agent's reasoning for harmonization decisions."""

    submission: Optional[dict[str, Any]] = None
    """Final submission with harmonized values."""
