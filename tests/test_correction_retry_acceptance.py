"""Adversarial merge retry contracts against real offline persistence.

These tests reuse the existing geometry and moto fixture, inject one failed
boundary, then retry the public handler. They deliberately avoid depending on
the operation journal's representation or its internal stage names.
"""

from typing import Any

import pytest
from test_merge_receipt_lambda import (
    EVENT,
    IMAGE_ID,
    keys,
    merge,
    queue_messages,
)

pytest_plugins = ("test_merge_receipt_lambda",)


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
    lifecycle_env: Any, failure: Any
) -> None:
    """A persisted output must not force retry to allocate another receipt."""
    env = lifecycle_env
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
    "method", ["delete_receipt", "purge_receipt_children", "update_image"]
)
def test_cleanup_failure_resumes_without_rebuilding_output(
    lifecycle_env: Any, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Fail once before/after source deletion, then finish the saved output."""
    env = lifecycle_env
    original = getattr(env.db, method)
    failed_once = False

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed_once
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
    lifecycle_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing downstream recompute must stay retryable after source removal."""
    env = lifecycle_env
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
    lifecycle_env: Any,
) -> None:
    """A duplicate succeeds even though the original receipts are gone."""
    env = lifecycle_env
    first = merge.handler(EVENT, None)
    _assert_settled(env, first)
    before = keys(env.db)
    replay = merge.handler(EVENT, None)
    assert replay == first
    assert keys(env.db) == before
    assert env.native.call_count == 1
    for queue in env.queues:
        assert not queue_messages(env, queue)


def test_overlapping_delivery_cannot_enter_cleanup_while_owner_embeds(
    lifecycle_env: Any,
) -> None:
    """A second invocation cannot mutate a currently owned output."""
    env = lifecycle_env
    nested_results = []
    entered = False

    def embed(**_kwargs: Any) -> dict[str, int]:
        nonlocal entered
        if not entered:
            entered = True
            nested = merge.handler(EVENT, None)
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
