"""Adversarial merge retry contracts against real offline persistence.

These tests reuse the existing geometry and moto fixture, inject one failed
boundary, then retry the public handler. They deliberately avoid depending on
the operation journal's representation or its internal stage names.
"""

from dataclasses import replace
from typing import Any

import pytest
from test_merge_receipt_lambda import (
    EVENT,
    IMAGE_ID,
    db,
    keys,
    lifecycle_env,
    merge,
    mock_aws_services,
    queue_messages,
)

# Re-export fixtures locally; a pytest plugin would affect unrelated tests.
__all__ = ["db", "lifecycle_env", "mock_aws_services"]


@pytest.fixture(name="merge_case")
def _merge_case(request: pytest.FixtureRequest) -> Any:
    """Use the shared fixture without shadowing its imported Python name."""
    return request.getfixturevalue("lifecycle_env")


def _assert_settled(env: Any, result: dict[str, Any]) -> None:
    """One output replaces the same pair and its derived work is queued."""
    assert result["status"] == "success", result
    output_id = result["new_receipt_id"]
    surviving = env.db.get_receipts_from_image_consistent(IMAGE_ID)
    assert {record.receipt_id for record in surviving} == {3, output_id}
    assert env.db.get_image(IMAGE_ID).receipt_count == 2
    for source_id in EVENT["receipt_ids"]:
        assert not any(
            key.startswith(
                (f"RECEIPT#{source_id:05d}#", f"RECEIPT#{source_id}#")
            )
            for key in keys(env.db)
        )
    for queue in env.queues:
        assert queue_messages(env, queue)


@pytest.mark.parametrize("failure", [{"failed": 1}, RuntimeError("offline")])
def test_native_failure_retries_same_output_with_reversed_sources(
    merge_case: Any, failure: Any
) -> None:
    """A persisted output must not force retry to allocate another receipt."""
    env = merge_case
    env.native.side_effect = [failure, {"written": 6, "failed": 0}]
    failed = merge.handler(EVENT, None)
    assert failed["status"] == "error", failed
    allocated = {
        record.receipt_id
        for record in env.db.get_receipts_from_image_consistent(IMAGE_ID)
    } - {1, 2, 3}
    assert len(allocated) == 1
    assert all(
        env.db.receipt_exists_consistent(IMAGE_ID, source_id)
        for source_id in (1, 2)
    )

    result = merge.handler({**EVENT, "receipt_ids": [2, 1]}, None)
    assert result.get("new_receipt_id") in allocated, result
    _assert_settled(env, result)


@pytest.mark.parametrize(
    "method",
    [
        "delete_receipt",
        "purge_receipt_children",
        "update_receipt_merge_image",
    ],
)
def test_cleanup_failure_resumes_without_rebuilding_output(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Fail once before/after source deletion, then finish the saved output."""
    env = merge_case
    original = getattr(env.db, method)
    failed_once = False

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed_once
        if method == "purge_receipt_children" and args[1] not in (1, 2):
            return original(*args, **kwargs)
        if not failed_once:
            failed_once = True
            raise RuntimeError(f"offline {method} interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(env.db, method, fail_once)
    failed = merge.handler(EVENT, None)
    assert failed_once
    assert failed["status"] == "error", failed
    assert env.native.call_count == 1

    result = merge.handler(EVENT, None)
    _assert_settled(env, result)
    assert env.native.call_count == 1


def test_queue_failure_after_source_deletion_resumes_same_output(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing downstream recompute must stay retryable after source removal."""
    env = merge_case
    sqs_client = env.sqs
    original = sqs_client.send_message
    failed_once = False
    original_client = merge.boto3.client

    def client(service: str, *args: Any, **kwargs: Any) -> Any:
        if service == "sqs":
            return sqs_client
        return original_client(service, *args, **kwargs)

    def fail_once(**kwargs: Any) -> Any:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("offline queue interruption")
        return original(**kwargs)

    monkeypatch.setattr(merge.boto3, "client", client)
    monkeypatch.setattr(sqs_client, "send_message", fail_once)
    failed = merge.handler(EVENT, None)
    assert failed_once
    assert failed["status"] == "error", failed
    assert not env.db.receipt_exists_consistent(IMAGE_ID, 1)
    assert not env.db.receipt_exists_consistent(IMAGE_ID, 2)

    result = merge.handler(EVENT, None)
    _assert_settled(env, result)
    assert env.native.call_count == 1


def test_completed_redelivery_returns_result_without_writing_again(
    merge_case: Any,
) -> None:
    """A duplicate succeeds even though the original receipts are gone."""
    env = merge_case
    first = merge.handler(EVENT, None)
    _assert_settled(env, first)
    before = keys(env.db)
    replay = merge.handler(EVENT, None)
    assert replay == first
    assert keys(env.db) == before
    assert env.native.call_count == 1
    for queue in env.queues:
        assert not queue_messages(env, queue)


@pytest.mark.parametrize(
    "dependent", [False, True], ids=["same-operation", "unfinished-output"]
)
def test_overlapping_delivery_cannot_enter_cleanup_while_owner_embeds(
    merge_case: Any,
    dependent: bool,
) -> None:
    """A second invocation cannot retry or consume an unfinished output."""
    env = merge_case
    nested_results = []
    entered = False

    def embed(**kwargs: Any) -> dict[str, int]:
        nonlocal entered
        if not entered:
            entered = True
            nested_event = (
                {**EVENT, "receipt_ids": [3, kwargs["receipt_id"]]}
                if dependent
                else EVENT
            )
            nested = merge.handler(nested_event, None)
            nested_results.append(nested)
            assert nested["status"] == "error", nested
            assert all(
                env.db.receipt_exists_consistent(IMAGE_ID, source_id)
                for source_id in (1, 2)
            )
        return {"written": 6, "failed": 0}

    env.native.side_effect = lambda *_args, **kwargs: embed(**kwargs)
    result = merge.handler(EVENT, None)
    assert len(nested_results) == 1
    _assert_settled(env, result)
    assert env.native.call_count == 1


def test_completed_output_can_be_used_in_a_later_merge(
    merge_case: Any,
) -> None:
    """Reserving unfinished outputs must still allow completed merge chains."""
    env = merge_case
    first = merge.handler(EVENT, None)
    _assert_settled(env, first)
    result = merge.handler(
        {**EVENT, "receipt_ids": [3, first["new_receipt_id"]]}, None
    )
    assert result["status"] == "success", result
    output_id = result["new_receipt_id"]
    assert output_id != first["new_receipt_id"]
    assert {
        record.receipt_id
        for record in env.db.get_receipts_from_image_consistent(IMAGE_ID)
    } == {output_id}
    assert env.db.get_image(IMAGE_ID).receipt_count == 1
    assert env.native.call_count == 2


def test_disjoint_merges_serialize_image_count_updates(
    merge_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale count from one merge must not overwrite another merge's count."""
    env = merge_case
    env.db.add_receipt(replace(env.sources[1], receipt_id=4))
    for line in env.db.list_receipt_lines_from_receipt(IMAGE_ID, 1):
        env.db.add_receipt_line(replace(line, receipt_id=4))
    for word in env.db.list_receipt_words_from_receipt(IMAGE_ID, 1):
        env.db.add_receipt_word(replace(word, receipt_id=4))
    env.db.update_image(replace(env.image, receipt_count=4))
    update_image = env.db.update_receipt_merge_image
    other_event = {**EVENT, "receipt_ids": [3, 4]}
    nested_results = []
    entered = False

    def update_after_another_merge(operation: Any, image: Any) -> Any:
        nonlocal entered
        if not entered:
            entered = True
            nested_results.append(merge.handler(other_event, None))
        return update_image(operation, image)

    monkeypatch.setattr(
        env.db, "update_receipt_merge_image", update_after_another_merge
    )
    first = merge.handler(EVENT, None)
    assert first["status"] == "success", first
    survivors = env.db.get_receipts_from_image_consistent(IMAGE_ID)
    assert env.db.get_image(IMAGE_ID).receipt_count == len(survivors)
    assert len(nested_results) == 1
    assert nested_results[0]["status"] == "error", nested_results

    second = merge.handler(other_event, None)
    assert second["status"] == "success", second
    survivors = env.db.get_receipts_from_image_consistent(IMAGE_ID)
    assert {receipt.receipt_id for receipt in survivors} == {
        first["new_receipt_id"],
        second["new_receipt_id"],
    }
    assert env.db.get_image(IMAGE_ID).receipt_count == len(survivors) == 2
