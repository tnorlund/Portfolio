"""Additional merge crash-boundary and source preservation tests."""

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from test_merge_receipt_lambda import (
    EVENT,
    IMAGE_ID,
    db,
    geometry,
    lifecycle_env,
    merge,
    mock_aws_services,
    receipt,
)

from receipt_dynamo import ReceiptLetter, ReceiptWord, ReceiptWordLabel
from receipt_dynamo.data._receipt_merge import MERGE_LEASE_SECONDS
from receipt_upload import combine

# Local fixture aliases avoid changing unrelated root tests.
__all__ = ["db", "lifecycle_env", "mock_aws_services"]


@pytest.fixture(name="merge_case")
def _merge_case(request: pytest.FixtureRequest) -> Any:
    """Use the shared setup without shadowing an imported name."""
    return request.getfixturevalue("lifecycle_env")


@pytest.mark.parametrize("stage", ["READY", "COMPLETED"])
def test_checkpoint_response_loss_replays_durable_state(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """A write can succeed even when its response never reaches the handler."""
    env = merge_case
    checkpoint = env.db.checkpoint_receipt_merge
    interrupted = False

    def lose_response(current: Any, updated: Any) -> Any:
        nonlocal interrupted
        result = checkpoint(current, updated)
        if updated.status == stage and not interrupted:
            interrupted = True
            raise RuntimeError("checkpoint response lost")
        return result

    monkeypatch.setattr(env.db, "checkpoint_receipt_merge", lose_response)
    assert merge.handler(EVENT, None)["status"] == "error"
    assert env.db.get_receipt_merge(IMAGE_ID, [1, 2]).status == stage
    result = merge.handler(EVENT, None)
    assert result["status"] == "success", result
    assert result["new_receipt_id"] == 4
    assert env.native.call_count == 1
    assert env.db.get_image(IMAGE_ID).receipt_count == 2


def test_output_collision_preserves_unrelated_row_and_objects(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-merge producer can take the ID between journal and output puts."""
    env = merge_case
    create = combine.create_combined_receipt_records
    raw_key = f"raw/{IMAGE_ID}_RECEIPT_00004.png"
    unrelated = replace(receipt(4), raw_s3_key=raw_key)

    def race(**kwargs: Any) -> Any:
        env.db.add_receipt(unrelated)
        env.s3.put_object(Bucket="merge-raw", Key=raw_key, Body=b"unrelated")
        return create(**kwargs)

    monkeypatch.setattr(combine, "create_combined_receipt_records", race)
    result = merge.handler(EVENT, None)
    assert result["status"] == "error"
    assert env.db.get_receipt(IMAGE_ID, 4) == unrelated
    assert (
        env.s3.get_object(Bucket="merge-raw", Key=raw_key)["Body"].read()
        == b"unrelated"
    )
    assert all(
        env.db.receipt_exists_consistent(IMAGE_ID, rid) for rid in (1, 2)
    )
    env.native.assert_not_called()


def test_missing_ready_output_preserves_sources(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must not consume the sources if the staged output vanished."""
    env = merge_case
    checkpoint = env.db.checkpoint_receipt_merge

    def remove_output(current: Any, updated: Any) -> Any:
        saved = checkpoint(current, updated)
        if updated.status == "READY":
            env.db.delete_receipt(env.db.get_receipt(IMAGE_ID, 4))
        return saved

    monkeypatch.setattr(env.db, "checkpoint_receipt_merge", remove_output)
    result = merge.handler(EVENT, None)
    assert result["status"] == "error"
    assert "Merge output is missing" in result["error"]
    assert all(
        env.db.receipt_exists_consistent(IMAGE_ID, rid) for rid in (1, 2)
    )


def test_merge_uses_committed_snapshot_without_eventual_rereads(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lagging per-type reads cannot drop one fragment's words or metadata."""
    env = merge_case
    env.db.add_receipt_letter(
        ReceiptLetter(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=1,
            letter_id=1,
            text="M",
            **geometry(0.2, 0.8, 0.05, 0.02),
        )
    )
    env.db.add_receipt_word_label(
        ReceiptWordLabel(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="PRODUCT_NAME",
            reasoning="Offline source label",
            validation_status="VALID",
            timestamp_added=datetime.now(timezone.utc),
        )
    )
    for method in (
        "list_receipt_words_from_receipt",
        "list_receipt_letters_from_word",
        "list_receipt_word_labels_for_receipt",
        "get_receipt_place",
        "get_receipt_sections_from_receipt",
        "list_receipt_barcodes_from_receipt_consistent",
    ):
        monkeypatch.setattr(
            env.db,
            method,
            Mock(side_effect=AssertionError("source snapshot was bypassed")),
        )
    result = merge.handler(EVENT, None)
    assert result["status"] == "success", result
    assert result["words_merged"] == 4
    assert result["letters_merged"] == 1
    assert result["labels_merged"] == 1
    assert result["sections_migrated"] == 1
    assert result["barcodes_migrated"] == 2


def test_failed_word_transform_never_deletes_a_partial_source(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = merge_case
    transform = ReceiptWord.warp_transform

    def fail_second_source(
        word: ReceiptWord, *args: Any, **kwargs: Any
    ) -> Any:
        if word.receipt_id == 2:
            raise RuntimeError("cannot transform source 2")
        return transform(word, *args, **kwargs)

    monkeypatch.setattr(ReceiptWord, "warp_transform", fail_second_source)
    result = merge.handler(EVENT, None)
    assert result["status"] == "error", result
    assert "cannot transform source 2" in result["error"]
    assert all(
        env.db.receipt_exists_consistent(IMAGE_ID, rid) for rid in (1, 2)
    )
    env.native.assert_not_called()


def test_merge_lease_outlives_the_lambda_hard_timeout() -> None:
    """An expired lease cannot be reclaimed while its Lambda still executes."""
    path = (
        Path(__file__).resolve().parents[1]
        / "infra/merge_receipt_lambda/infrastructure.py"
    )
    tree = ast.parse(path.read_text())
    timeout = next(
        value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "lambda_config"
        and isinstance(node.value, ast.Dict)
        for key, value in zip(node.value.keys, node.value.values)
        if isinstance(key, ast.Constant)
        and key.value == "timeout"
        and isinstance(value, ast.Constant)
    )
    assert isinstance(timeout, int)
    assert MERGE_LEASE_SECONDS >= timeout + 60
