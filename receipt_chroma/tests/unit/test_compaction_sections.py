"""Unit tests for RECEIPT_SECTION compaction updates.

The consumer recomputes each affected receipt's row ``section_label``
metadata from the receipt's *current* ReceiptSection rows in DynamoDB
(never from the stream event image), mirroring the row-label path.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from receipt_chroma.compaction.sections import apply_section_updates
from receipt_dynamo.constants import ChromaDBCollection

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _message(image_id: str = IMAGE_ID, receipt_id: int = 1, **extra):
    event_name = extra.pop("event_name", "MODIFY")
    return SimpleNamespace(
        entity_type="RECEIPT_SECTION",
        entity_data={
            "entity_type": "RECEIPT_SECTION",
            "image_id": image_id,
            "receipt_id": receipt_id,
            **extra,
        },
        event_name=event_name,
    )


def _section(
    section_type: str,
    line_ids,
    confidence: float = 0.9,
    validation_status: str = "PENDING",
):
    return SimpleNamespace(
        section_type=section_type,
        line_ids=line_ids,
        confidence=confidence,
        validation_status=validation_status,
    )


def _chroma_with_rows(rows: dict[str, dict]):
    """Mock ChromaClient whose LINES collection returns the given rows."""
    collection_obj = Mock()
    collection_obj.get.return_value = {
        "ids": list(rows.keys()),
        "metadatas": list(rows.values()),
    }
    chroma_client = Mock()
    chroma_client.get_collection.return_value = collection_obj
    return chroma_client, collection_obj


def _dynamo_with_sections(sections):
    dynamo = Mock()
    dynamo.get_receipt_sections_from_receipt.return_value = sections
    return dynamo


@pytest.mark.unit
def test_recompute_stamps_plurality_from_fresh_state():
    """Labels come from DynamoDB's current sections, not the event."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1, 2]),
                "merchant_name": "Test",
            },
            "IMAGE#img#RECEIPT#00001#LINE#00003": {
                "row_line_ids": json.dumps([3]),
                "merchant_name": "Test",
            },
        }
    )
    dynamo = _dynamo_with_sections(
        [
            _section("HEADER", [1, 2]),
            _section("LINE_ITEMS", [3]),
        ]
    )

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert len(results) == 1
    assert results[0].error is None
    assert results[0].updated_count == 2
    kwargs = collection_obj.update.call_args.kwargs
    by_id = dict(zip(kwargs["ids"], kwargs["metadatas"]))
    # Chroma update() MERGES metadata, so only the delta is sent —
    # other keys (merchant_name, ...) survive untouched on the row.
    assert by_id["IMAGE#img#RECEIPT#00001#LINE#00001"] == {
        "section_label": "HEADER"
    }
    assert by_id["IMAGE#img#RECEIPT#00001#LINE#00003"] == {
        "section_label": "LINE_ITEMS"
    }


@pytest.mark.unit
def test_missing_rows_is_clean_noop():
    """Receipt not embedded yet (batch at OpenAI) -> no error, no update."""
    chroma, collection_obj = _chroma_with_rows({})
    dynamo = _dynamo_with_sections([_section("HEADER", [1])])

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert len(results) == 1
    assert results[0].error is None
    assert results[0].updated_count == 0
    collection_obj.update.assert_not_called()
    # Dynamo isn't queried when there is nothing to stamp
    dynamo.get_receipt_sections_from_receipt.assert_not_called()


@pytest.mark.unit
def test_invalid_section_demotion_removes_label():
    """QA flipping a section INVALID must strip stale section_label."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1]),
                "section_label": "HEADER",
                "merchant_name": "Test",
            }
        }
    )
    dynamo = _dynamo_with_sections(
        [_section("HEADER", [1], validation_status="INVALID")]
    )

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert results[0].updated_count == 1
    new_metadata = collection_obj.update.call_args.kwargs["metadatas"][0]
    # None deletes the key under Chroma's merge semantics
    assert new_metadata == {"section_label": None}


@pytest.mark.unit
def test_section_delete_removes_labels():
    """All sections deleted -> every stamped row loses section_label."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1]),
                "section_label": "HEADER",
            }
        }
    )
    dynamo = _dynamo_with_sections([])

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message(event_name="REMOVE")],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert results[0].updated_count == 1
    new_metadata = collection_obj.update.call_args.kwargs["metadatas"][0]
    assert new_metadata == {"section_label": None}


@pytest.mark.unit
def test_tie_leaves_label_unset():
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1, 2]),
                "section_label": "HEADER",
            }
        }
    )
    dynamo = _dynamo_with_sections(
        [
            _section("HEADER", [1]),
            _section("LINE_ITEMS", [2]),
        ]
    )

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert results[0].updated_count == 1
    new_metadata = collection_obj.update.call_args.kwargs["metadatas"][0]
    assert new_metadata == {"section_label": None}


@pytest.mark.unit
def test_unchanged_rows_are_not_rewritten():
    """Idempotent: reprocessing the same state writes nothing."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1]),
                "section_label": "HEADER",
            }
        }
    )
    dynamo = _dynamo_with_sections([_section("HEADER", [1])])

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert results[0].updated_count == 0
    collection_obj.update.assert_not_called()


@pytest.mark.unit
def test_storm_collapses_to_one_recompute_per_receipt():
    """N events for one receipt cost one Dynamo query + one pass."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1]),
            }
        }
    )
    dynamo = _dynamo_with_sections([_section("HEADER", [1])])

    messages = [
        _message(section_type=f"TYPE_{i}", event_name="MODIFY")
        for i in range(25)
    ]
    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=messages,
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert len(results) == 1
    assert dynamo.get_receipt_sections_from_receipt.call_count == 1
    assert collection_obj.get.call_count == 1


@pytest.mark.unit
def test_words_collection_is_noop():
    chroma = Mock()
    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.WORDS,
        logger=Mock(),
        dynamo_client=Mock(),
    )
    assert results == []
    chroma.get_collection.assert_not_called()


@pytest.mark.unit
def test_missing_collection_is_noop():
    """Fresh environment without a lines collection must not error."""
    chroma = Mock()
    chroma.get_collection.side_effect = ValueError("no such collection")
    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=Mock(),
    )
    assert results == []


@pytest.mark.unit
def test_unexpected_get_collection_error_propagates():
    """Transient/unknown Chroma failures must NOT be acknowledged as a
    missing collection; they propagate so the batch retries."""
    chroma = Mock()
    chroma.get_collection.side_effect = RuntimeError("client closed")
    with pytest.raises(RuntimeError):
        apply_section_updates(
            chroma_client=chroma,
            section_messages=[_message()],
            collection=ChromaDBCollection.LINES,
            logger=Mock(),
            dynamo_client=Mock(),
        )


@pytest.mark.unit
def test_extra_receipts_recomputed_without_section_messages():
    """Receipts touched by a delta merge are restamped even when no
    section message shares the batch (stale-delta-overwrite guard)."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1]),
                "section_label": "STALE",
            }
        }
    )
    dynamo = _dynamo_with_sections([_section("HEADER", [1])])

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
        extra_receipts=[(IMAGE_ID, 1)],
    )

    assert len(results) == 1
    assert results[0].updated_count == 1
    new_metadata = collection_obj.update.call_args.kwargs["metadatas"][0]
    assert new_metadata == {"section_label": "HEADER"}


@pytest.mark.unit
def test_dynamo_failure_records_error_for_retry():
    """A failed section fetch must NOT strip labels; it must error so
    the SQS message is retried."""
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00001": {
                "row_line_ids": json.dumps([1]),
                "section_label": "HEADER",
            }
        }
    )
    dynamo = Mock()
    dynamo.get_receipt_sections_from_receipt.side_effect = RuntimeError(
        "dynamo down"
    )

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert len(results) == 1
    assert results[0].error is not None
    collection_obj.update.assert_not_called()


@pytest.mark.unit
def test_real_chroma_update_semantics_stamp_and_strip(tmp_path):
    """Against a real ChromaDB collection: update() merges metadata, so
    the delta-only payload preserves other keys, and None deletes the
    key (a full-metadata payload minus the key would NOT delete it)."""
    chromadb = pytest.importorskip("chromadb")
    # PersistentClient with a unique path: chroma's shared-system cache
    # keys by path, so this cannot collide with other tests' clients.
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection_obj = client.create_collection("lines-test")
    row_id = "IMAGE#img#RECEIPT#00001#LINE#00001"
    collection_obj.add(
        ids=[row_id],
        embeddings=[[0.1, 0.2]],
        metadatas=[
            {
                "image_id": IMAGE_ID,
                "receipt_id": 1,
                "row_line_ids": json.dumps([1]),
                "merchant_name": "Test",
                "section_label": "STALE",
            }
        ],
    )
    chroma = Mock()
    chroma.get_collection.return_value = collection_obj

    def run(sections):
        dynamo = _dynamo_with_sections(sections)
        return apply_section_updates(
            chroma_client=chroma,
            section_messages=[_message()],
            collection=ChromaDBCollection.LINES,
            logger=Mock(),
            dynamo_client=dynamo,
        )

    # Restamp: STALE -> HEADER, other keys preserved
    results = run([_section("HEADER", [1])])
    assert results[0].updated_count == 1
    metadata = collection_obj.get(ids=[row_id], include=["metadatas"])[
        "metadatas"
    ][0]
    assert metadata["section_label"] == "HEADER"
    assert metadata["merchant_name"] == "Test"

    # Strip: all sections INVALID -> key actually deleted on the row
    results = run([_section("HEADER", [1], validation_status="INVALID")])
    assert results[0].updated_count == 1
    metadata = collection_obj.get(ids=[row_id], include=["metadatas"])[
        "metadatas"
    ][0]
    assert "section_label" not in metadata
    assert metadata["merchant_name"] == "Test"


@pytest.mark.unit
def test_legacy_row_without_row_line_ids_uses_line_id():
    chroma, collection_obj = _chroma_with_rows(
        {
            "IMAGE#img#RECEIPT#00001#LINE#00007": {
                "line_id": 7,
            }
        }
    )
    dynamo = _dynamo_with_sections([_section("FOOTER", [7])])

    results = apply_section_updates(
        chroma_client=chroma,
        section_messages=[_message()],
        collection=ChromaDBCollection.LINES,
        logger=Mock(),
        dynamo_client=dynamo,
    )

    assert results[0].updated_count == 1
    new_metadata = collection_obj.update.call_args.kwargs["metadatas"][0]
    assert new_metadata["section_label"] == "FOOTER"
