"""Offline tests for bounded receipt embedding backfill behavior."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from receipt_dynamo.constants import ValidationStatus
from scripts.backfill_receipt_embeddings import (
    build_requests,
    collect_requests,
    main,
    wait_for_written_keys,
)

from receipt_embeddings import ScoredItem

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


@dataclass
class Geometry:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict[str, float]
    word_id: int | None = None

    def calculate_centroid(self) -> tuple[float, float]:
        return (
            self.bounding_box["x"] + self.bounding_box["width"] / 2,
            self.bounding_box["y"] + self.bounding_box["height"] / 2,
        )


def test_build_requests_preserves_display_text_and_context_input() -> None:
    line = Geometry(
        IMAGE_ID,
        1,
        2,
        "COFFEE 12.99",
        {"x": 0.1, "y": 0.8, "width": 0.5, "height": 0.05},
    )
    word = Geometry(
        IMAGE_ID,
        1,
        2,
        "COFFEE",
        {"x": 0.1, "y": 0.8, "width": 0.2, "height": 0.05},
        word_id=3,
    )
    details = SimpleNamespace(
        receipt=SimpleNamespace(image_id=IMAGE_ID, receipt_id=1),
        lines=[line],
        words=[word],
        labels=[
            SimpleNamespace(
                line_id=2,
                word_id=3,
                validation_status=ValidationStatus.VALID.value,
            )
        ],
        place=SimpleNamespace(merchant_name="Fixture Mart", place_id="p1"),
    )
    sections = [SimpleNamespace(line_ids=[2], section_type="ITEMS")]

    requests = build_requests(details, sections, {})

    assert requests[0].text == "COFFEE 12.99"
    assert requests[0].embedding_input == "<EDGE>\nCOFFEE 12.99\n<EDGE>"
    assert requests[0].section_type == "ITEMS"
    assert requests[1].text == "COFFEE"
    assert requests[1].embedding_input == "<EDGE> <EDGE> COFFEE <EDGE> <EDGE>"
    assert requests[1].label_status == "validated"


def test_absent_receipt_is_skipped_without_aborting_scope() -> None:
    class MissingDynamo:
        def get_receipt_details(self, *_args: Any) -> Any:
            raise RuntimeError("receipt absent")

    requests, skips = collect_requests(
        MissingDynamo(),
        [{"image_id": IMAGE_ID, "receipt_id": 1}],
        {},
    )

    assert requests == []
    assert skips == [
        {"receipt": f"{IMAGE_ID}#00001", "reason": "receipt absent"}
    ]


def test_searchability_wait_checks_only_this_runs_exact_keys() -> None:
    line_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    word_key = f"{line_key}#WORD#00003"

    class SearchClient:
        last_request_bytes = 40960

        def __init__(self) -> None:
            self.gets: list[str] = []

        def get_vector(self, key: str) -> list[float]:
            self.gets.append(key)
            return [0.01] * 1536

        def search(self, *_args: Any, **kwargs: Any) -> list[ScoredItem]:
            index = kwargs["index"]
            target = word_key if index == "word-embeddings" else line_key
            return [
                ScoredItem("foreign-key", 0.0),
                ScoredItem(target, 0.01),
            ]

    client = SearchClient()
    result = wait_for_written_keys(
        client,
        [line_key, word_key],
        timeout_seconds=0,
        sample_size=2,
    )

    assert result["status"] == "searchable"
    assert set(client.gets) == {line_key, word_key}
    assert all(value["searchable"] for value in result["results"])


def test_backfill_refuses_non_dev_table_before_any_io() -> None:
    with pytest.raises(SystemExit, match="only 'ReceiptsTable-dc5be22'"):
        main(["--table-name", "ReceiptsTable-prod", "--limit", "1"])


def test_applied_backfill_requires_explicit_limit_before_any_io() -> None:
    with pytest.raises(SystemExit, match="requires an explicit --limit"):
        main(["--apply"])
