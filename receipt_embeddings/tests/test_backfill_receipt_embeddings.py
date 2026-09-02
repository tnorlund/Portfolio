"""Offline tests for bounded receipt embedding backfill behavior."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from receipt_dynamo.constants import ValidationStatus

pytest.importorskip(
    "chromadb",
    reason="imports the backfill script's chroma source; the CI receipt_embeddings leg is chromadb-free",
)

from receipt_embeddings import ScoredItem
from receipt_embeddings.writer import (
    EmbeddingWriteFailure,
    EmbeddingWriteReport,
    EmbeddingWriteRequest,
)
from scripts.backfill_receipt_embeddings import (
    EXIT_GLOBAL_WRITE_FAILURE,
    EXIT_VERIFICATION_FAILURE,
    ChromaVectorSource,
    apply_stored_vectors,
    build_chroma_source,
    build_requests,
    collect_requests,
    determine_exit_code,
    main,
    resolve_vector_source,
    verify_written_items,
    wait_for_written_keys,
)

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


@dataclass
class Geometry:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict[str, float]
    word_id: int | None = None
    extracted_data: dict[str, Any] | None = None

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


def test_build_requests_marks_invalid_only_words_validated() -> None:
    """INVALID-only words carry a terminal verdict and must stay in the
    validated population, or the word index's filter would drop exactly
    the counterexamples similar_labeled_words needs (E3 review P1-2)."""
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
                validation_status=ValidationStatus.INVALID.value,
            )
        ],
        place=SimpleNamespace(merchant_name="Fixture Mart", place_id="p1"),
    )
    sections = [SimpleNamespace(line_ids=[2], section_type="ITEMS")]

    requests = build_requests(details, sections, {})

    assert requests[1].label_status == "validated"


def test_repair_label_status_reclassifies_existing_word_embeddings(
    monkeypatch,
) -> None:
    """Metadata repair for pre-rule backfills (E3 review P1-B): existing
    word embedding items are re-aggregated from their CURRENT label rows
    — idempotent, metadata-only (vectors untouched), line embeddings and
    already-correct words ignored, never creating items."""
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.entities import EMBEDDING_DIMENSIONS
    from receipt_dynamo.entities.receipt_embedding import (
        ReceiptLineEmbedding,
        ReceiptWordEmbedding,
    )
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
    from scripts.backfill_receipt_embeddings import repair_label_status

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("DYNAMODB_ENDPOINT_URL", raising=False)
    table = "ReceiptsTable-dc5be22"
    vector = [0.001] * EMBEDDING_DIMENSIONS
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=table,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=table)

        stale = ReceiptWordEmbedding(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=3,
            text="12.99",
            merchant_name="Fixture Mart",
            label_status="none",
            word_vector=list(vector),
        )
        current = ReceiptWordEmbedding(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=4,
            text="TOTAL",
            merchant_name="Fixture Mart",
            label_status="validated",
            word_vector=list(vector),
        )
        line = ReceiptLineEmbedding(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            text="TOTAL 12.99",
            merchant_name="Fixture Mart",
            place_id="p1",
            row_line_ids=[2],
            section_type="",
            line_vector=list(vector),
        )
        invalid_only = ReceiptWordLabel(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="GRAND_TOTAL",
            reasoning="human rejected",
            timestamp_added="2026-08-31T00:00:00+00:00",
            validation_status="INVALID",
            label_proposed_by="human",
        )
        valid = ReceiptWordLabel(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=4,
            label="GRAND_TOTAL",
            reasoning="matches printed total",
            timestamp_added="2026-08-31T00:00:00+00:00",
            validation_status="VALID",
            label_proposed_by="human",
        )
        for entity in (stale, current, line, invalid_only, valid):
            client.put_item(TableName=table, Item=entity.to_item())

        dynamo = DynamoClient(table_name=table, region="us-east-1")
        receipts = [{"image_id": IMAGE_ID, "receipt_id": 1}]

        dry = repair_label_status(dynamo, receipts, apply=False)
        assert dry["dry_run"] is True
        assert dry["words_examined"] == 2
        assert dry["unchanged"] == 1
        assert dry["updates_planned"] == 1
        assert dry["updates_applied"] == 0
        untouched = client.get_item(TableName=table, Key=stale.key)["Item"]
        assert untouched["label_status"]["S"] == "none"

        applied = repair_label_status(dynamo, receipts, apply=True)
        assert applied["updates_planned"] == 1
        assert applied["updates_applied"] == 1
        assert applied["exit_code"] == 0
        repaired = client.get_item(TableName=table, Key=stale.key)["Item"]
        assert repaired["label_status"]["S"] == "validated"
        # Metadata-only: the stored vector is byte-identical.
        assert repaired[ReceiptWordEmbedding.VECTOR_ATTRIBUTE] == (
            stale.to_item()[ReceiptWordEmbedding.VECTOR_ATTRIBUTE]
        )

        second = repair_label_status(dynamo, receipts, apply=True)
        assert second["updates_planned"] == 0
        assert second["updates_applied"] == 0


def test_build_requests_computes_fetch_join_anchor_metadata() -> None:
    """Line requests carry the Chroma-writer-computed normalized anchors
    for the row's words (Round C fetch-join ruling)."""
    line = Geometry(
        IMAGE_ID,
        1,
        2,
        "CALL 555-123-4567",
        {"x": 0.1, "y": 0.8, "width": 0.5, "height": 0.05},
    )
    phone_word = Geometry(
        IMAGE_ID,
        1,
        2,
        "555-123-4567",
        {"x": 0.1, "y": 0.8, "width": 0.2, "height": 0.05},
        word_id=1,
        extracted_data={"type": "phone", "value": "555-123-4567"},
    )
    address_word = Geometry(
        IMAGE_ID,
        1,
        2,
        "123 Main St, Henderson NV 89014",
        {"x": 0.4, "y": 0.8, "width": 0.2, "height": 0.05},
        word_id=2,
        extracted_data={
            "type": "address",
            "value": "123 Main St, Henderson NV 89014",
        },
    )
    details = SimpleNamespace(
        receipt=SimpleNamespace(image_id=IMAGE_ID, receipt_id=1),
        lines=[line],
        words=[phone_word, address_word],
        labels=[],
        place=SimpleNamespace(merchant_name="Fixture Mart", place_id="p1"),
    )

    requests = build_requests(details, [], {})

    line_request = requests[0]
    assert line_request.kind == "line"
    assert line_request.normalized_phone_10 == "5551234567"
    assert "MAIN ST" in line_request.normalized_full_address
    entity_item = line_request.build_entity([0.01] * 1536).to_item()
    assert entity_item["normalized_phone_10"] == {"S": "5551234567"}


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
        {
            "receipt": f"{IMAGE_ID}#00001",
            "reason": "receipt absent",
            "category": "error:RuntimeError",
        }
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


def _line_request(line_id: int, vector: list[float] | None = None):
    return EmbeddingWriteRequest(
        kind="line",
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        text="COFFEE",
        row_line_ids=(line_id,),
        vector=vector,
    )


def test_vector_source_auto_prefers_chroma_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CHROMA_CLOUD_API_KEY",
        "CHROMA_CLOUD_TENANT",
        "CHROMA_CLOUD_DATABASE",
    ):
        monkeypatch.setenv(name, "value")
    assert resolve_vector_source("auto") == "chroma"
    monkeypatch.delenv("CHROMA_CLOUD_API_KEY")
    assert resolve_vector_source("auto") == "openai"
    assert resolve_vector_source("fixture") == "fixture"


def test_chroma_source_refuses_non_dev_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHROMA_CLOUD_API_KEY", "key")
    monkeypatch.setenv("CHROMA_CLOUD_TENANT", "tenant")
    monkeypatch.setenv("CHROMA_CLOUD_DATABASE", "receipt_prod")
    with pytest.raises(SystemExit, match="only 'receipt_dev'"):
        build_chroma_source()


def test_chroma_source_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CHROMA_CLOUD_API_KEY",
        "CHROMA_CLOUD_TENANT",
        "CHROMA_CLOUD_DATABASE",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="CHROMA_CLOUD_API_KEY"):
        build_chroma_source()


def test_chroma_vector_source_batches_by_collection() -> None:
    line_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    word_key = f"{line_key}#WORD#00003"

    class FakeChroma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[str]]] = []

        def get(self, collection_name: str, ids: list[str], **_: Any):
            self.calls.append((collection_name, list(ids)))
            return {"ids": list(ids), "embeddings": [[0.5] * 3] * len(ids)}

        def close(self) -> None:
            pass

    fake = FakeChroma()
    source = ChromaVectorSource(fake)
    vectors = source.vectors_for([line_key, word_key])

    assert set(vectors) == {line_key, word_key}
    assert vectors[line_key] == [0.5, 0.5, 0.5]
    assert ("lines", [line_key]) in fake.calls
    assert ("words", [word_key]) in fake.calls


def test_apply_stored_vectors_skip_reports_missing_vectors() -> None:
    covered_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    requests = [
        _line_request(2),
        _line_request(3),
        _line_request(4, vector=[0.25] * 1536),
    ]

    filled, skips = apply_stored_vectors(
        requests,
        {covered_key: [0.5] * 1536},
        missing_reason="missing_stored_vector",
    )

    assert [request.line_id for request in filled] == [2, 4]
    assert filled[0].vector == [0.5] * 1536
    assert filled[1].vector == [0.25] * 1536
    assert skips == [
        {
            "key": f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00003",
            "reason": "missing_stored_vector",
        }
    ]


def test_backfill_refuses_non_dev_table_before_any_io() -> None:
    with pytest.raises(SystemExit, match="only 'ReceiptsTable-dc5be22'"):
        main(["--table-name", "ReceiptsTable-prod", "--limit", "1"])


def test_applied_backfill_requires_explicit_limit_before_any_io() -> None:
    with pytest.raises(SystemExit, match="requires an explicit --limit"):
        main(["--apply"])


def _failure(key: str) -> EmbeddingWriteFailure:
    return EmbeddingWriteFailure(key=key, stage="embed", error="denied")


def test_global_failure_pattern_exits_with_distinct_code() -> None:
    """Zero writes + nonzero failures (bad credentials, outage) is never
    a success exit."""
    report = EmbeddingWriteReport(failures=[_failure("k1"), _failure("k2")])

    code = determine_exit_code(report, {"status": "not_needed"})

    assert code == EXIT_GLOBAL_WRITE_FAILURE
    assert code != 0


def test_idempotent_rerun_with_zero_writes_exits_zero() -> None:
    report = EmbeddingWriteReport(skipped_existing_keys=["k1", "k2"])

    assert determine_exit_code(report, {"status": "not_needed"}) == 0


def test_idempotent_rerun_with_residual_failures_exits_zero() -> None:
    """Regression (judge run 2, 2026-09-01): a rerun over a completed
    corpus skips everything as existing while the same residual
    unfillable items the first run tolerated fail again. That is not the
    global-outage pattern — skipped-existing items are proof the corpus
    is there — so the rerun must exit 0 exactly like the first run."""
    report = EmbeddingWriteReport(
        skipped_existing_keys=["k1", "k2", "k3"],
        failures=[_failure("k4"), _failure("k5")],
    )

    assert determine_exit_code(report, {"status": "not_needed"}) == 0


def test_per_item_failures_beside_successful_writes_exit_zero() -> None:
    report = EmbeddingWriteReport(
        written_keys=["k1"], failures=[_failure("k2")]
    )

    assert determine_exit_code(report, {"status": "verified"}) == 0


def test_unverified_written_items_exit_with_verification_code() -> None:
    report = EmbeddingWriteReport(written_keys=["k1"])

    code = determine_exit_code(report, {"status": "missing"})

    assert code == EXIT_VERIFICATION_FAILURE
    assert code not in (0, EXIT_GLOBAL_WRITE_FAILURE)


class _VerifyDynamo:
    """batch_get_item fake: returns the subset of requested keys it holds,
    optionally leaving keys unprocessed once."""

    def __init__(
        self, present: set[str], unprocessed_once: bool = False
    ) -> None:
        self._present = present
        self._unprocessed_once = unprocessed_once
        self.calls = 0

    def batch_get_item(self, RequestItems: dict) -> dict:
        self.calls += 1
        (table,) = RequestItems
        request = RequestItems[table]
        assert request["ConsistentRead"] is True
        keys = request["Keys"]
        if self._unprocessed_once:
            self._unprocessed_once = False
            return {
                "Responses": {table: []},
                "UnprocessedKeys": {table: {"Keys": keys}},
            }
        found = [key for key in keys if key["SK"]["S"] in self._present]
        return {"Responses": {table: found}, "UnprocessedKeys": {}}


def test_verify_written_items_confirms_every_key_strongly() -> None:
    line_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    client = _VerifyDynamo({"RECEIPT#00001#LINE#00002#EMBEDDING"})

    result = verify_written_items(
        client, "ReceiptsTable-dc5be22", [line_key], sleep=lambda _: None
    )

    assert result == {
        "status": "verified",
        "checked": 1,
        "missing_keys": [],
    }


def test_verify_written_items_never_false_passes_a_missing_item() -> None:
    present = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    missing = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00099"
    client = _VerifyDynamo({"RECEIPT#00001#LINE#00002#EMBEDDING"})

    result = verify_written_items(
        client,
        "ReceiptsTable-dc5be22",
        [present, missing],
        sleep=lambda _: None,
    )

    assert result["status"] == "missing"
    assert result["missing_keys"] == [missing]


def test_verify_written_items_retries_unprocessed_keys() -> None:
    line_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    client = _VerifyDynamo(
        {"RECEIPT#00001#LINE#00002#EMBEDDING"}, unprocessed_once=True
    )

    result = verify_written_items(
        client, "ReceiptsTable-dc5be22", [line_key], sleep=lambda _: None
    )

    assert result["status"] == "verified"
    assert client.calls == 2


def test_verify_written_items_reports_missing_on_read_outage() -> None:
    class BrokenDynamo:
        def batch_get_item(self, **_: Any) -> dict:
            raise RuntimeError("endpoint unreachable")

    line_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"

    result = verify_written_items(
        BrokenDynamo(),
        "ReceiptsTable-dc5be22",
        [line_key],
        sleep=lambda _: None,
    )

    assert result["status"] == "missing"
    assert result["missing_keys"] == [line_key]
