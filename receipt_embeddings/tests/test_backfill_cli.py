"""CLI guards for the embedding-item backfill."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from receipt_embeddings.quotas import MAX_SEARCH_RESULTS
from receipt_embeddings.vector_client import ScoredItem

from scripts.backfill_embedding_items import _wait_searchable
from scripts.backfill_embedding_items import main as backfill_main

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


@pytest.mark.unit
def test_backfill_refuses_prod_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    with pytest.raises(SystemExit, match="refusing to query DynamoDB"):
        backfill_main(
            ["--table-name", "ReceiptsTable-d7ff76a", "--limit", "1"]
        )


@pytest.mark.unit
def test_backfill_limit_below_floor_requires_flag() -> None:
    with pytest.raises(SystemExit, match="pass --allow-under-floor"):
        backfill_main(["--limit", "5"])


@pytest.mark.unit
def test_wait_searchable_matches_this_run_key_and_ignores_foreign() -> None:
    ours = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00003"
    foreign = f"IMAGE#{IMAGE_ID}#RECEIPT#00099#LINE#00001"
    item = MagicMock()
    item.line_vector = [1.0, 0.0]
    item.vector_search_key = ours
    client = MagicMock()
    client.search.return_value = [
        ScoredItem(foreign, 0.4, {}),
        ScoredItem(ours, 0.0, {}),
    ]
    report = _wait_searchable(client, item, timeout_s=1.0, interval_s=0.01)
    assert report["searchable"] is True
    assert report["key"] == ours
    assert report["ignored_foreign_neighbors"] == 1
    assert client.search.call_args.args[2] == MAX_SEARCH_RESULTS


@pytest.mark.unit
def test_backfill_refuses_prod_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    with pytest.raises(SystemExit, match="refusing to query DynamoDB"):
        backfill_main(
            ["--table-name", "ReceiptsTable-d7ff76a", "--limit", "1"]
        )


@pytest.mark.unit
def test_backfill_limit_below_floor_requires_flag() -> None:
    with pytest.raises(SystemExit, match="pass --allow-under-floor"):
        backfill_main(["--limit", "5"])
