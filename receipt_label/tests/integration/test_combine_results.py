import asyncio
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
)
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord

from receipt_label.langchain.currency_validation import (
    combine_results,
    load_saved_state,
)
from receipt_label.langchain.models.currency_validation import (
    CurrencyLabel,
    CurrencyLabelType,
    LineItemLabel,
    LineItemLabelType,
)


def _geom_box(x: float, y: float, w: float, h: float) -> Dict[str, float]:
    return {"x": x, "y": y, "width": w, "height": h}


def _corner(x: float, y: float) -> Dict[str, float]:
    return {"x": x, "y": y}


class FakeDynamoClient:
    def __init__(
        self,
        words_by_line: Dict[int, List[ReceiptWord]],
        existing: List[ReceiptWordLabel],
    ):
        self._words_by_line = words_by_line
        self._existing = list(existing)
        self.added: List[ReceiptWordLabel] = []
        self.updated: List[ReceiptWordLabel] = []

    # Used by create_receipt_word_labels_from_currency_labels
    def list_receipt_words_from_line(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> List[ReceiptWord]:
        return self._words_by_line.get(line_id, [])

    # Used by combine_results
    def list_receipt_word_labels_for_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabel], Optional[Dict[str, Any]]]:
        return (list(self._existing), None)

    # Used by save path (not required for combine here, but keep for completeness)
    def add_receipt_word_labels(self, labels: List[ReceiptWordLabel]) -> None:
        self.added.extend(labels)

    def add_receipt_word_label(self, label: ReceiptWordLabel) -> None:
        self.added.append(label)

    def update_receipt_word_labels(
        self, labels: List[ReceiptWordLabel]
    ) -> None:
        self.updated.extend(labels)

    def update_receipt_word_label(self, label: ReceiptWordLabel) -> None:
        self.updated.append(label)


@pytest.mark.asyncio
async def test_combine_results_adds_and_updates_basic():
    image_id = "b6e7af49-3802-46f2-822e-bbd49cd55ada"
    receipt_id = 123

    # Minimal but valid ReceiptLine for line_id=1
    line = ReceiptLine(
        receipt_id=receipt_id,
        image_id=image_id,
        line_id=1,
        text="YOGURT 8.99",
        bounding_box=_geom_box(0.1, 0.1, 0.5, 0.05),
        top_right=_corner(0.6, 0.1),
        top_left=_corner(0.1, 0.1),
        bottom_right=_corner(0.6, 0.15),
        bottom_left=_corner(0.1, 0.15),
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )

    # Words on the line: YOGURT and 8.99
    words_by_line = {
        1: [
            ReceiptWord(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=1,
                word_id=1,
                text="YOGURT",
                bounding_box=_geom_box(0.1, 0.1, 0.2, 0.05),
                top_right=_corner(0.3, 0.1),
                top_left=_corner(0.1, 0.1),
                bottom_right=_corner(0.3, 0.15),
                bottom_left=_corner(0.1, 0.15),
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.99,
            ),
            ReceiptWord(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=1,
                word_id=2,
                text="8.99",
                bounding_box=_geom_box(0.35, 0.1, 0.1, 0.05),
                top_right=_corner(0.45, 0.1),
                top_left=_corner(0.35, 0.1),
                bottom_right=_corner(0.45, 0.15),
                bottom_left=_corner(0.35, 0.15),
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.99,
            ),
        ]
    }

    # Existing label: LINE_TOTAL already present for word_id=2; will be updated (merge reasoning)
    existing = [
        ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=1,
            word_id=2,
            label="LINE_TOTAL",
            reasoning="old",
            timestamp_added=datetime.now(),
            validation_status="PENDING",
            label_proposed_by="previous",
        )
    ]

    client = FakeDynamoClient(words_by_line=words_by_line, existing=existing)

    # Phase results
    currency_labels: List[CurrencyLabel] = [
        CurrencyLabel(
            line_text="8.99",
            amount=8.99,
            label_type=CurrencyLabelType.LINE_TOTAL,
            line_ids=[1],
            confidence=0.9,
            reasoning="found amount",
        )
    ]
    line_item_labels: List[LineItemLabel] = [
        LineItemLabel(
            word_text="YOGURT",
            label_type=LineItemLabelType.PRODUCT_NAME,
            confidence=0.85,
            reasoning="found product",
        )
    ]

    state = {
        "receipt_id": f"{image_id}/{receipt_id}",
        "image_id": image_id,
        "lines": [line],
        "formatted_text": "",
        "dynamo_client": client,
        "currency_labels": currency_labels,
        "line_item_labels": line_item_labels,
    }

    result = await combine_results(state, save_dev_state=False)

    adds = result.get("receipt_word_labels_to_add", [])
    updates = result.get("receipt_word_labels_to_update", [])

    # Expect 1 add (PRODUCT_NAME on word_id=1) and 1 update (LINE_TOTAL on word_id=2)
    assert len(adds) == 1, f"unexpected adds: {adds}"
    assert len(updates) == 1, f"unexpected updates: {updates}"

    add = adds[0]
    assert add.label == "PRODUCT_NAME"
    assert add.word_id == 1
    assert add.image_id == image_id and add.receipt_id == receipt_id

    upd = updates[0]
    assert upd.label == "LINE_TOTAL"
    assert upd.word_id == 2
    # Reasoning should be merged
    assert "old" in (upd.reasoning or "") and "found amount" in (
        upd.reasoning or ""
    )


@pytest.mark.asyncio
async def test_combine_results_from_saved_state_json_if_available():
    # Try to load a provided dev.state JSON; skip if not present
    path = "/Users/tnorlund/GitHub/example/dev.states/state_combine_results_start_b6e7af49-3802-46f2-822e-bbd49cd55ada_1_20250904_095038.json"
    try:
        saved = load_saved_state(path)
    except Exception:
        pytest.skip("saved state file not available on this machine")

    image_id = "b6e7af49-3802-46f2-822e-bbd49cd55ada"
    # Reconstruct minimal lines from saved state
    saved_lines = saved.get("lines", [])

    # Use SimpleNamespace to avoid needing full geometry fields
    lines = [
        SimpleNamespace(line_id=ln.get("line_id", 1)) for ln in saved_lines
    ] or [SimpleNamespace(line_id=1)]

    # Reconstruct CurrencyLabel and LineItemLabel from saved state
    currency_labels = []
    for it in saved.get("currency_labels", []):
        try:
            currency_labels.append(
                CurrencyLabel(
                    line_text=it.get("line_text", ""),
                    amount=float(it.get("amount", 0.0)),
                    label_type=CurrencyLabelType(it.get("label_type")),
                    line_ids=list(it.get("line_ids", [])),
                    confidence=float(it.get("confidence", 0.0)),
                    reasoning=it.get("reasoning", ""),
                )
            )
        except Exception:
            # Skip malformed entries
            pass

    line_item_labels = []
    for it in saved.get("line_item_labels", []):
        try:
            line_item_labels.append(
                LineItemLabel(
                    word_text=it.get("word_text", it.get("line_text", "")),
                    label_type=LineItemLabelType(it.get("label_type")),
                    confidence=float(it.get("confidence", 0.0)),
                    reasoning=it.get("reasoning", ""),
                )
            )
        except Exception:
            pass

    # Build words that include tokens from labels so mapping can succeed
    words_by_line: Dict[int, List[ReceiptWord]] = {}

    def ensure_word(line_id: int, word_id: int, text: str):
        words_by_line.setdefault(line_id, []).append(
            ReceiptWord(
                receipt_id=1,
                image_id=image_id,
                line_id=line_id,
                word_id=word_id,
                text=text,
                bounding_box=_geom_box(0.1, 0.1, 0.1, 0.05),
                top_right=_corner(0.2, 0.1),
                top_left=_corner(0.1, 0.1),
                bottom_right=_corner(0.2, 0.15),
                bottom_left=_corner(0.1, 0.15),
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.99,
            )
        )

    word_id_seq = 1
    for lbl in currency_labels:
        for lid in lbl.line_ids or [1]:
            # Add the amount token and split line_text tokens
            ensure_word(lid, word_id_seq, str(lbl.line_text))
            word_id_seq += 1
            for tok in str(lbl.line_text).split():
                ensure_word(lid, word_id_seq, tok)
                word_id_seq += 1

    for lbl in line_item_labels:
        for lid in lbl.line_ids or [1]:
            ensure_word(lid, word_id_seq, lbl.word_text)
            word_id_seq += 1

    client = FakeDynamoClient(words_by_line=words_by_line, existing=[])

    state = {
        "receipt_id": f"{image_id}/1",
        "image_id": image_id,
        "lines": lines,
        "formatted_text": saved.get("formatted_text", ""),
        "dynamo_client": client,
        "currency_labels": currency_labels,
        "line_item_labels": line_item_labels,
    }

    result = await combine_results(state, save_dev_state=False)
    # Sanity: we get at least some add proposals based on reconstructed data
    adds = result.get("receipt_word_labels_to_add", [])
    assert isinstance(adds, list)
    # It's possible reconstruction yields zero if saved state has no labels; allow zero but ensure no crash
    assert result.get("confidence_score") is not None
