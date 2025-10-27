from __future__ import annotations

import json
import time
import operator
from pathlib import Path
from typing import Any, Dict, List, Optional, Annotated, TYPE_CHECKING

from pydantic import BaseModel, Field, ConfigDict
from receipt_dynamo.entities import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_label.langchain.models import CurrencyLabel, LineItemLabel

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata


class CurrencyAnalysisState(BaseModel):
    """Pydantic v2 state model for the workflow.

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
    existing_word_labels: Optional[List[ReceiptWordLabel]] = None
    receipt_metadata: Optional[Any] = None  # ReceiptMetadata - using Any to avoid circular import

    # Phase results
    currency_labels: List[CurrencyLabel] = Field(default_factory=list)
    transaction_labels: List = Field(default_factory=list)  # Transaction context labels
    line_item_labels: Annotated[List[LineItemLabel], operator.add] = Field(
        default_factory=list
    )

    # Final
    discovered_labels: List[Any] = Field(default_factory=list)
    confidence_score: float = 0.0
    processing_time: float = 0.0
    
    # Error handling (NEW)
    error_count: int = 0
    last_error: Optional[str] = None
    partial_results: bool = False
    
    # Metadata validation (NEW)
    metadata_validation: Optional[Any] = None
    
    # Store original metadata for potential updates after graph completes
    _metadata_for_update: Optional[Any] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
        revalidate_instances="never",
    )

    @classmethod
    def from_graph(cls, state: Dict[str, Any]) -> "CurrencyAnalysisState":
        return cls(
            receipt_id=state.get("receipt_id", ""),
            image_id=state.get("image_id", ""),
            lines=state.get("lines", []) or [],
            words=state.get("words", []) or [],
            formatted_text=state.get("formatted_text", "") or "",
            dynamo_client=state.get("dynamo_client"),
            existing_word_labels=state.get("existing_word_labels"),
            receipt_metadata=state.get("receipt_metadata"),
            currency_labels=state.get("currency_labels", []) or [],
            transaction_labels=state.get("transaction_labels", []) or [],
            line_item_labels=state.get("line_item_labels", []) or [],
            discovered_labels=state.get("discovered_labels", []) or [],
            confidence_score=float(state.get("confidence_score", 0.0) or 0.0),
            processing_time=float(state.get("processing_time", 0.0) or 0.0),
            error_count=state.get("error_count", 0),
            last_error=state.get("last_error"),
            partial_results=state.get("partial_results", False),
        )

    def to_graph(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "image_id": self.image_id,
            "lines": self.lines,
            "words": self.words,
            "formatted_text": self.formatted_text,
            "dynamo_client": self.dynamo_client,
            "existing_word_labels": self.existing_word_labels,
            "receipt_metadata": self.receipt_metadata,
            "currency_labels": self.currency_labels,
            "transaction_labels": self.transaction_labels,
            "line_item_labels": self.line_item_labels,
            "discovered_labels": self.discovered_labels,
            "confidence_score": self.confidence_score,
            "processing_time": self.processing_time,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "partial_results": self.partial_results,
        }

    def to_serializable(self) -> Dict[str, Any]:
        """
        Convert the state to a serializable format.
        """
        return {
            "receipt_id": self.receipt_id,
            "image_id": self.image_id,
            "formatted_text": self.formatted_text,
            "dynamo_client": None,
            "existing_word_labels_count": len(self.existing_word_labels or []),
            "lines": [dict(l) for l in (self.lines or [])],
            "words": [dict(w) for w in (self.words or [])],
            "currency_labels": [
                _dump_label(x) for x in (self.currency_labels or [])
            ],
            "line_item_labels": [
                _dump_label(x) for x in (self.line_item_labels or [])
            ],
            "discovered_labels": [
                _dump_label(x) for x in (self.discovered_labels or [])
            ],
            "confidence_score": self.confidence_score,
            "processing_time": self.processing_time,
        }


def _dump_label(label: Any) -> Dict[str, Any]:
    if hasattr(label, "model_dump"):
        return label.model_dump(mode="json")
    try:
        return dict(label)
    except Exception:
        return {
            "line_text": getattr(label, "line_text", None),
            "amount": getattr(label, "amount", None),
            "label_type": getattr(
                getattr(label, "label_type", ""),
                "value",
                getattr(label, "label_type", ""),
            ),
            "line_ids": getattr(label, "line_ids", []),
            "confidence": getattr(label, "confidence", 0.0),
            "reasoning": getattr(label, "reasoning", None),
            "word_text": getattr(label, "word_text", None),
        }


def _dump_lines(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [dict(l) for l in (state.get("lines") or [])]


def _dump_words(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [dict(w) for w in (state.get("words") or [])]


def to_serializable(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert the state to a serializable format.
    """
    model = CurrencyAnalysisState.from_graph(state)
    return model.to_serializable()


def save_json(
    state: Dict[str, Any],
    stage: str,
    receipt_id: Optional[str] = None,
    output_dir: str = "./dev.states",
) -> Dict[str, str]:
    """
    Save the state to a JSON file.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    rid = receipt_id or state.get("receipt_id")
    rid_part = f"_{str(rid).replace('/', '_')}" if rid else ""
    base = f"state_{stage}{rid_part}_{ts}"

    serializable = to_serializable(state)

    json_path = out_dir / f"{base}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    meta = {
        "stage": stage,
        "receipt_id": rid,
        "timestamp": ts,
        "state_keys": list(state.keys()),
        "currency_labels_count": len(state.get("currency_labels") or []),
        "line_item_labels_count": len(state.get("line_item_labels") or []),
        "discovered_labels_count": len(state.get("discovered_labels") or []),
        "confidence_score": float(state.get("confidence_score") or 0.0),
        "saved_files": {"json": str(json_path)},
    }
    meta_path = out_dir / f"{base}_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return {"json": str(json_path), "metadata": str(meta_path)}


def load_json(file_path: str) -> Dict[str, Any]:
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"State file not found: {file_path}")
    if p.suffix != ".json":
        raise ValueError(f"Unsupported file format: {p.suffix}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)
