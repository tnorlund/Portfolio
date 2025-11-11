"""State model for merchant metadata creation workflow."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict

from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
    from receipt_dynamo.data.dynamo_client import DynamoClient


class MetadataCreationState(BaseModel):
    """Pydantic v2 state model for the metadata creation workflow.

    Allows arbitrary types for external entities. Use from_graph/to_graph
    to bridge with LangGraph's dict-based state.
    """

    # Inputs
    receipt_id: str
    image_id: str
    lines: List[ReceiptLine] = Field(default_factory=list)
    words: List[ReceiptWord] = Field(default_factory=list)
    formatted_text: str = ""
    dynamo_client: Any | None = None

    # Extracted merchant information
    extracted_merchant_name: Optional[str] = None
    extracted_address: Optional[str] = None
    extracted_phone: Optional[str] = None
    extracted_merchant_words: List[str] = Field(default_factory=list)

    # Google Places search results
    places_search_results: List[Dict[str, Any]] = Field(default_factory=list)
    selected_place: Optional[Dict[str, Any]] = None

    # Final output
    receipt_metadata: Optional[Any] = None  # ReceiptMetadata - using Any to avoid circular import
    metadata_created: bool = False
    error_message: Optional[str] = None

    # Processing metadata
    processing_time: float = 0.0
    error_count: int = 0
    last_error: Optional[str] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
        revalidate_instances="never",
    )

    @classmethod
    def from_graph(cls, state: Dict[str, Any]) -> "MetadataCreationState":
        """Create state from LangGraph dict state."""
        return cls(
            receipt_id=state.get("receipt_id", ""),
            image_id=state.get("image_id", ""),
            lines=state.get("lines", []) or [],
            words=state.get("words", []) or [],
            formatted_text=state.get("formatted_text", "") or "",
            dynamo_client=state.get("dynamo_client"),
            extracted_merchant_name=state.get("extracted_merchant_name"),
            extracted_address=state.get("extracted_address"),
            extracted_phone=state.get("extracted_phone"),
            extracted_merchant_words=state.get("extracted_merchant_words", []) or [],
            places_search_results=state.get("places_search_results", []) or [],
            selected_place=state.get("selected_place"),
            receipt_metadata=state.get("receipt_metadata"),
            metadata_created=state.get("metadata_created", False),
            error_message=state.get("error_message"),
            processing_time=float(state.get("processing_time", 0.0) or 0.0),
            error_count=state.get("error_count", 0),
            last_error=state.get("last_error"),
        )

    def to_graph(self) -> Dict[str, Any]:
        """Convert state to LangGraph dict state."""
        return {
            "receipt_id": self.receipt_id,
            "image_id": self.image_id,
            "lines": self.lines,
            "words": self.words,
            "formatted_text": self.formatted_text,
            "dynamo_client": self.dynamo_client,
            "extracted_merchant_name": self.extracted_merchant_name,
            "extracted_address": self.extracted_address,
            "extracted_phone": self.extracted_phone,
            "extracted_merchant_words": self.extracted_merchant_words,
            "places_search_results": self.places_search_results,
            "selected_place": self.selected_place,
            "receipt_metadata": self.receipt_metadata,
            "metadata_created": self.metadata_created,
            "error_message": self.error_message,
            "processing_time": self.processing_time,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }

