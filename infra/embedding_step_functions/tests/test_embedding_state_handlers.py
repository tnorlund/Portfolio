"""Focused lifecycle tests for the lightweight embedding handlers."""

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

from receipt_chroma.embedding.openai.batch_status import (
    handle_cancelled_status,
    release_batch_receipts_for_retry,
)
from receipt_dynamo.constants import BatchStatus, EmbeddingStatus

ROOT = Path(__file__).parents[1]


def _load(name: str, relative_path: str):
    spec = spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_active_batches_keeps_provider_in_progress_states() -> None:
    module = _load("embedding_list_active", "simple_lambdas/list_pending/handler.py")
    now = datetime.now(timezone.utc)
    summaries = {
        status: [
            SimpleNamespace(
                batch_id=status.value,
                submitted_at=now,
            )
        ]
        for status in module.ACTIVE_BATCH_STATUSES
    }

    class FakeDynamo:
        def get_batch_summaries_by_status(
            self, *, status, batch_type, limit, last_evaluated_key
        ):
            del batch_type, limit, last_evaluated_key
            return summaries[status], None

    found = module._list_active_batches(FakeDynamo(), "WORD_EMBEDDING")

    assert {summary.batch_id for summary in found} == {
        BatchStatus.PENDING.value,
        BatchStatus.VALIDATING.value,
        BatchStatus.IN_PROGRESS.value,
        BatchStatus.FINALIZING.value,
        BatchStatus.CANCELING.value,
    }


def test_reconciler_releases_orphans_and_marks_noise() -> None:
    module = _load(
        "embedding_backfill_control",
        "simple_lambdas/backfill_control/handler.py",
    )
    orphan = SimpleNamespace(
        image_id="image-a",
        receipt_id=1,
        line_id=1,
        embedding_status=EmbeddingStatus.PENDING.value,
        is_noise=False,
    )
    active = SimpleNamespace(
        image_id="image-b",
        receipt_id=2,
        line_id=2,
        embedding_status=EmbeddingStatus.PENDING.value,
        is_noise=False,
    )
    noise = SimpleNamespace(
        image_id="image-c",
        receipt_id=3,
        line_id=3,
        embedding_status=EmbeddingStatus.NONE.value,
        is_noise=True,
    )

    class FakeDynamo:
        def __init__(self):
            self.updated = []

        def update_receipt_lines(self, entities):
            self.updated.extend(entities)

    dynamo = FakeDynamo()
    result = module._reconcile_entities(
        dynamo,
        "lines",
        [orphan, active, noise],
        {("image-b", 2)},
    )

    assert orphan.embedding_status == EmbeddingStatus.NONE.value
    assert active.embedding_status == EmbeddingStatus.PENDING.value
    assert noise.embedding_status == EmbeddingStatus.NOISE.value
    assert result == {"updated": 2, "released": 1, "noise_marked": 1}


def test_finalizer_requires_a_real_delta_and_completed_batch() -> None:
    module = _load(
        "embedding_mark_complete",
        "simple_lambdas/mark_batches_complete/handler.py",
    )
    poll_results = [
        {
            "batch_id": "complete",
            "batch_status": "completed",
            "action": "process_results",
            "collection": "words",
            "delta_key": "deltas/one.zip",
            "embedded_items": [
                {
                    "image_id": "image-a",
                    "receipt_id": 1,
                    "line_id": 2,
                    "word_id": 3,
                }
            ],
        },
        {
            "batch_id": "waiting",
            "batch_status": "in_progress",
            "action": "wait",
            "collection": "words",
            "delta_key": None,
            "embedded_items": [],
        },
    ]

    assert module._completed_batch_ids(poll_results) == ["complete"]
    assert module._items_to_finalize(poll_results, "words") == [
        {
            "image_id": "image-a",
            "receipt_id": 1,
            "line_id": 2,
            "word_id": 3,
        }
    ]


def test_partial_receipt_claim_is_rolled_back_after_a_collision(monkeypatch) -> None:
    module = _load(
        "embedding_find_unembedded",
        "simple_lambdas/find_unembedded/handler.py",
    )

    class FakeEntity:
        def __init__(self, index: int):
            self.key = {"PK": {"S": "IMAGE#one"}, "SK": {"S": f"LINE#{index}"}}

    class FakeDynamo:
        def __init__(self):
            self.calls = []

        def transact_write_items(self, **kwargs):
            self.calls.append(kwargs["TransactItems"])
            if len(self.calls) == 2:
                raise module.ClientError(
                    {
                        "Error": {
                            "Code": "TransactionCanceledException",
                            "Message": "claim lost",
                        },
                        "CancellationReasons": [{"Code": "ConditionalCheckFailed"}],
                    },
                    "TransactWriteItems",
                )

    fake_dynamo = FakeDynamo()
    monkeypatch.setattr(module.boto3, "client", lambda _service: fake_dynamo)

    assert module._claim_receipt("table", [FakeEntity(i) for i in range(26)]) is False
    assert len(fake_dynamo.calls) == 3
    rollback = fake_dynamo.calls[2]
    assert len(rollback) == 25
    assert all(
        ":none_gsi" in item["Update"]["ExpressionAttributeValues"] for item in rollback
    )


def test_discovery_returns_a_bounded_receipt_page(monkeypatch) -> None:
    module = _load(
        "embedding_find_unembedded_bounded",
        "simple_lambdas/find_unembedded/handler.py",
    )
    entities = {
        key: SimpleNamespace(
            image_id=f"image-{key}",
            receipt_id=1,
            line_id=1,
            word_id=1,
            is_noise=False,
        )
        for key in ("a", "b", "c")
    }

    class FakeDynamo:
        def __init__(self):
            self.query_calls = []

        def query(self, **kwargs):
            self.query_calls.append(kwargs)
            return {"Items": [{"id": {"S": key}} for key in ("a", "b", "c")]}

    fake_dynamo = FakeDynamo()
    monkeypatch.setattr(module.boto3, "client", lambda _service: fake_dynamo)
    monkeypatch.setattr(
        module.ReceiptWord,
        "from_item",
        staticmethod(lambda item: entities[item["id"]["S"]]),
    )

    batches, has_more = module._discover_batches("table", "words", 1)

    assert [[entity.image_id for entity in batch] for batch in batches] == [["image-a"]]
    assert has_more is True
    assert len(fake_dynamo.query_calls) == 1
    assert fake_dynamo.query_calls[0]["Limit"] == 500


def test_terminal_batch_manifest_releases_unreported_pending_words() -> None:
    pending = SimpleNamespace(
        line_id=1,
        word_id=1,
        embedding_status=EmbeddingStatus.PENDING.value,
    )
    success = SimpleNamespace(
        line_id=1,
        word_id=2,
        embedding_status=EmbeddingStatus.SUCCESS.value,
    )

    class FakeDynamo:
        def __init__(self):
            self.updated = []

        def list_receipt_words_from_receipt(self, image_id, receipt_id):
            assert (image_id, receipt_id) == ("image-a", 1)
            return [pending, success]

        def update_receipt_words(self, words):
            self.updated.extend(words)

    dynamo = FakeDynamo()
    summary = SimpleNamespace(receipt_refs=[("image-a", 1)])

    assert release_batch_receipts_for_retry(summary, "word", dynamo) == 1
    assert pending.embedding_status == EmbeddingStatus.NONE.value
    assert success.embedding_status == EmbeddingStatus.SUCCESS.value
    assert dynamo.updated == [pending]


def test_canceling_batch_keeps_claims_until_provider_is_terminal() -> None:
    summary = SimpleNamespace(
        status=BatchStatus.IN_PROGRESS,
        batch_type="WORD_EMBEDDING",
        receipt_refs=[("image-a", 1)],
        submitted_at=datetime.now(timezone.utc),
    )

    class FakeDynamo:
        def get_batch_summary(self, batch_id):
            assert batch_id == "batch-a"
            return summary

        def update_batch_summary(self, updated):
            assert updated is summary

        def list_receipt_words_from_receipt(self, _image_id, _receipt_id):
            raise AssertionError("CANCELING is provider-active and must keep claims")

    result = handle_cancelled_status("batch-a", "openai-a", "canceling", FakeDynamo())

    assert summary.status == BatchStatus.CANCELING
    assert result["marked_for_retry"] == 0
