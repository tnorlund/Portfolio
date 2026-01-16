# pylint: disable=redefined-outer-name,unused-variable
"""Unit tests for CompactionRun entity."""
from datetime import datetime
from uuid import uuid4

import pytest

from receipt_dynamo.constants import CompactionState
from receipt_dynamo.entities.compaction_run import (
    CompactionRun,
    item_to_compaction_run,
)


@pytest.fixture
def example_run() -> CompactionRun:
    return CompactionRun(
        run_id=str(uuid4()),
        image_id=str(uuid4()),
        receipt_id=1,
        lines_delta_prefix="chromadb/lines/delta/abc/",
        words_delta_prefix="chromadb/words/delta/abc/",
        lines_state=CompactionState.PENDING.value,
        words_state=CompactionState.PENDING.value,
    )


@pytest.mark.unit
def test_compaction_run_init_defaults(example_run: CompactionRun):
    assert isinstance(example_run.created_at, str)
    assert example_run.lines_state == CompactionState.PENDING.value
    assert example_run.words_state == CompactionState.PENDING.value
    # Optional timestamps and errors default
    assert example_run.lines_started_at is None
    assert example_run.lines_finished_at is None
    assert example_run.words_started_at is None
    assert example_run.words_finished_at is None
    assert example_run.lines_error == ""
    assert example_run.words_error == ""


@pytest.mark.unit
def test_compaction_run_key_and_indexes(example_run: CompactionRun):
    key = example_run.key
    assert key["PK"]["S"] == f"IMAGE#{example_run.image_id}"
    assert key["SK"]["S"].startswith(
        f"RECEIPT#{example_run.receipt_id:05d}#COMPACTION_RUN#"
    )

    gsi1 = example_run.gsi1_key()
    assert gsi1["GSI1PK"]["S"] == "RUNS"
    assert gsi1["GSI1SK"]["S"].startswith("CREATED_AT#")


@pytest.mark.unit
def test_compaction_run_to_item_and_back(example_run: CompactionRun):
    item = example_run.to_item()
    # Required fields present
    for k in (
        "PK",
        "SK",
        "TYPE",
        "lines_delta_prefix",
        "words_delta_prefix",
        "created_at",
    ):
        assert k in item
    # Body convenience fields
    assert item["run_id"]["S"] == example_run.run_id
    assert item["image_id"]["S"] == example_run.image_id
    assert item["receipt_id"]["N"] == str(example_run.receipt_id)

    reconstructed = item_to_compaction_run(item)
    assert reconstructed.run_id == example_run.run_id
    assert reconstructed.image_id == example_run.image_id
    assert reconstructed.receipt_id == example_run.receipt_id
    assert reconstructed.lines_delta_prefix == example_run.lines_delta_prefix
    assert reconstructed.words_delta_prefix == example_run.words_delta_prefix
    assert reconstructed.lines_state == example_run.lines_state
    assert reconstructed.words_state == example_run.words_state


@pytest.mark.unit
def test_compaction_run_datetime_handling():
    now = datetime(2024, 1, 1, 12, 0, 0)
    cr = CompactionRun(
        run_id=str(uuid4()),
        image_id=str(uuid4()),
        receipt_id=123,
        lines_delta_prefix="chromadb/lines/delta/x/",
        words_delta_prefix="chromadb/words/delta/x/",
        lines_started_at=now,
        words_finished_at=now,
    )
    item = cr.to_item()
    assert item["lines_started_at"]["S"] == now.isoformat()
    assert item["words_finished_at"]["S"] == now.isoformat()


@pytest.mark.unit
def test_compaction_run_invalid_states():
    with pytest.raises(ValueError):
        CompactionRun(
            run_id=str(uuid4()),
            image_id=str(uuid4()),
            receipt_id=1,
            lines_delta_prefix="a/",
            words_delta_prefix="b/",
            lines_state="NOT_A_STATE",
        )


@pytest.mark.unit
def test_compaction_run_invalid_ids():
    # invalid image uuid
    with pytest.raises(ValueError):
        CompactionRun(
            run_id=str(uuid4()),
            image_id="not-a-uuid",
            receipt_id=1,
            lines_delta_prefix="a/",
            words_delta_prefix="b/",
        )
    # invalid run uuid
    with pytest.raises(ValueError):
        CompactionRun(
            run_id="not-a-uuid",
            image_id=str(uuid4()),
            receipt_id=1,
            lines_delta_prefix="a/",
            words_delta_prefix="b/",
        )
    # invalid receipt id
    with pytest.raises(ValueError):
        CompactionRun(
            run_id=str(uuid4()),
            image_id=str(uuid4()),
            receipt_id=0,
            lines_delta_prefix="a/",
            words_delta_prefix="b/",
        )


@pytest.mark.unit
def test_compaction_run_invalid_prefixes():
    with pytest.raises(ValueError):
        CompactionRun(
            run_id=str(uuid4()),
            image_id=str(uuid4()),
            receipt_id=1,
            lines_delta_prefix="",
            words_delta_prefix="b/",
        )
    with pytest.raises(ValueError):
        CompactionRun(
            run_id=str(uuid4()),
            image_id=str(uuid4()),
            receipt_id=1,
            lines_delta_prefix="a/",
            words_delta_prefix="",
        )


@pytest.mark.unit
def test_compaction_run_parser_invalid_keys():
    bad = {
        "PK": {"S": "INVALID"},
        "SK": {"S": "STATUS"},
        "TYPE": {"S": "COMPACTION_RUN"},
        "lines_delta_prefix": {"S": "a/"},
        "words_delta_prefix": {"S": "b/"},
        "created_at": {"S": "2024-01-01T00:00:00"},
    }
    with pytest.raises(ValueError):
        item_to_compaction_run(bad)


@pytest.mark.unit
def test_compaction_run_str_and_repr(example_run: CompactionRun):
    assert "CompactionRun(" in str(example_run)
    assert example_run.run_id in str(example_run)
    r = repr(example_run)
    assert example_run.run_id in r
    assert example_run.image_id in r
