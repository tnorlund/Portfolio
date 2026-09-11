"""Native receipt tracing must work with hosted tracing completely disabled."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import receipt_dynamo
import receipt_dynamo.entities
from langsmith.utils import ContextThreadPoolExecutor

from receipt_upload import line_items, tracing, vector_search
from receipt_upload.label_validation import llm_runner
from receipt_upload.label_validation.llm_validator import (
    LabelDecision,
    LabelValidationResponse,
    LLMBatchValidator,
)
from receipt_upload.label_validation.validator import (
    ValidationDecision,
    ValidationResult,
)
from receipt_upload.merchant_resolution import embedding_processor


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> list:
    records = []
    monkeypatch.setenv("RECEIPT_TRACE_BUCKET", "receipt-traces")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "retained-but-disabled")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(
        tracing.boto3,
        "client",
        lambda name: SimpleNamespace(
            put_object=lambda **kwargs: records.append(kwargs)
        ),
    )

    def reject_hosted(**kwargs: Any) -> None:
        pytest.fail("Hosted tracing was used")

    monkeypatch.setattr(tracing, "hosted_traceable", reject_hosted)
    return records


def test_native_trace_keeps_parentage_across_lambda_worker_threads(
    sink: list,
) -> None:
    @tracing.traceable(name="label_validation_llm")
    def child(word_text: str) -> dict:
        return {"word_text": word_text, "decision": "valid"}

    @tracing.traceable(
        name="receipt_processing",
        metadata={"image_id": "image", "receipt_id": 1},
    )
    def root() -> dict:
        with ContextThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(child, ["milk", "bread"]))
        return {"success": True, "results": results}

    root()
    assert len(sink) == 1
    rows = [json.loads(line) for line in sink[0]["Body"].decode().splitlines()]
    parent = next(row for row in rows if row["is_root"])
    children = [row for row in rows if not row["is_root"]]
    assert len(children) == 2
    assert all(row["trace_id"] == parent["trace_id"] for row in rows)
    assert all(row["parent_run_id"] == parent["id"] for row in children)
    assert json.loads(parent["extra"])["metadata"]["image_id"] == "image"
    assert all(row["start_time"].endswith("+00:00") for row in rows)


def test_errors_are_persisted_and_re_raised_without_leaking_context(
    sink: list,
) -> None:
    @tracing.traceable(name="receipt_processing")
    def run(fail: bool) -> dict:
        if fail:
            raise ValueError("provider failed")
        return {"success": True}

    with pytest.raises(ValueError, match="provider failed"):
        run(True)
    run(False)
    rows = [json.loads(item["Body"]) for item in sink]
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "provider failed"
    assert rows[1]["status"] == "success"
    assert rows[0]["trace_id"] != rows[1]["trace_id"]
    assert rows[1]["parent_run_id"] is None


def test_s3_failure_is_not_reported_as_success(
    sink: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(**kwargs: Any) -> None:
        raise RuntimeError("S3 unavailable")

    monkeypatch.setattr(
        tracing.boto3, "client", lambda name: SimpleNamespace(put_object=fail)
    )

    @tracing.traceable(name="receipt_processing")
    def run() -> dict:
        return {"success": True}

    with pytest.raises(RuntimeError, match="S3 unavailable"):
        run()
    assert tracing._trace.get() is None


def test_key_alone_does_not_enable_hosted_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECEIPT_TRACE_BUCKET", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "old-key")
    assert tracing.hosted_enabled() is False
    assert tracing.capture_enabled() is False


def test_real_llm_producer_maps_prompt_indexes_to_receipt_word_ids(
    sink: list,
) -> None:
    validator = LLMBatchValidator.__new__(LLMBatchValidator)
    response = LabelValidationResponse(
        decisions=[
            LabelDecision(
                index=1,
                decision="VALID",
                label="PRODUCT_NAME",
                confidence="high",
                reasoning="Product word",
            )
        ]
    )
    validator.structured_llm = SimpleNamespace(
        invoke=lambda messages: response
    )
    pending = [
        {
            "line_id": 7,
            "word_id": 2,
            "label": "PRODUCT_NAME",
            "word_text": "Milk",
        },
        {
            "line_id": 9,
            "word_id": 3,
            "label": "PRODUCT_NAME",
            "word_text": "Bread",
        },
    ]
    assert validator._call_llm_with_tracing("prompt", pending) is response
    row = json.loads(sink[0]["Body"])
    payload = json.loads(row["outputs"])
    assert row["name"] == "llm_batch_validation"
    assert [
        (r["line_id"], r["word_id"], r["decision"])
        for r in payload["validations"]
    ] == [
        (7, 2, "NEEDS_REVIEW"),
        (9, 3, "VALID"),
    ]


@pytest.mark.parametrize(
    "label,proposer,decision,expected",
    [
        ("PRODUCT_NAME", "model", ValidationDecision.AUTO_VALIDATE, "VALID"),
        (
            "LINE_TOTAL",
            "geometry_line_items",
            ValidationDecision.KEEP_PENDING,
            "NEEDS_REVIEW",
        ),
    ],
)
def test_real_similarity_worker_emits_native_word_decisions(
    sink: list,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    proposer: str,
    decision: ValidationDecision,
    expected: str,
) -> None:
    dynamo = Mock()
    dynamo.get_receipt_sections_from_receipt.return_value = []
    monkeypatch.setattr(receipt_dynamo, "DynamoClient", lambda table: dynamo)
    monkeypatch.setattr(
        receipt_dynamo.entities,
        "ReceiptWord",
        lambda **kw: SimpleNamespace(**kw),
    )
    monkeypatch.setattr(
        receipt_dynamo.entities,
        "ReceiptWordLabel",
        lambda **kw: SimpleNamespace(**kw),
    )
    monkeypatch.setattr(
        vector_search, "vector_search_client", lambda **kw: Mock()
    )
    monkeypatch.setattr(
        embedding_processor,
        "_prepare_pending_core_labels",
        lambda **kw: kw["word_labels"],
    )
    monkeypatch.setattr(
        line_items, "dedupe_grand_total", lambda *args, **kw: []
    )
    monkeypatch.setattr(
        line_items, "propose_line_item_labels", lambda *args: []
    )
    monkeypatch.setattr(
        line_items, "propose_product_names", lambda *args, **kw: []
    )
    monkeypatch.setattr(
        line_items, "reclassify_mislabeled_totals", lambda *args: ([], [])
    )
    monkeypatch.setattr(
        embedding_processor,
        "LightweightLabelValidator",
        lambda **kw: SimpleNamespace(
            validate_label=lambda **args: ValidationResult(
                decision=decision,
                confidence=0.9,
                consensus_label=label,
                matching_count=3,
                reason="Matched",
            ),
        ),
    )
    result = embedding_processor._run_words_pipeline_worker(
        words_data=[{"line_id": 7, "word_id": 2, "text": "Milk"}],
        word_labels_data=[
            {
                "line_id": 7,
                "word_id": 2,
                "label": label,
                "validation_status": "PENDING",
                "label_proposed_by": proposer,
            }
        ],
        word_embeddings_list=[[0.1]],
        image_id="image",
        receipt_id=1,
        table_name="test-table",
    )
    assert result["success"] is True
    row = json.loads(sink[0]["Body"])
    assert row["name"] == "label_validation_similarity"
    decision = json.loads(row["outputs"])["validations"][0]
    assert (
        decision["line_id"],
        decision["word_id"],
        decision["decision"],
    ) == (7, 2, expected)
    assert decision["final_label"] == label
    persisted = dynamo.update_receipt_word_label.call_args.args[0]
    assert persisted.validation_status == expected


def test_async_validation_keeps_original_trace_id_across_invocations(
    sink: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    @tracing.traceable(
        name="receipt_processing",
        metadata={"image_id": "image", "receipt_id": 1},
    )
    def produce() -> dict:
        return llm_runner.build_async_payload(
            llm_needed=[],
            words=[],
            image_id="image",
            receipt_id=1,
            table_name="test-table",
            lightweight_validator=None,
            word_embedding_cache={},
        )

    payload = produce()
    parent = json.loads(sink[0]["Body"])
    assert payload["native_trace_context"]["trace_id"] == parent["trace_id"]

    @tracing.traceable(name="llm_batch_validation")
    def llm() -> dict:
        return {
            "validations": [{"line_id": 1, "word_id": 1, "decision": "VALID"}]
        }

    def apply(**kwargs: Any) -> int:
        llm()
        return 1

    monkeypatch.setattr(llm_runner, "apply_llm_results", apply)
    assert llm_runner.apply_async_payload(payload, None) == 1
    assert sink[0]["Key"] != sink[1]["Key"]
    rows = [json.loads(line) for line in sink[1]["Body"].decode().splitlines()]
    assert all(row["trace_id"] == parent["trace_id"] for row in rows)
    child = next(
        row for row in rows if row["name"] == "async_label_validation"
    )
    assert child["parent_run_id"] == parent["id"]
    assert child["is_root"] is False
