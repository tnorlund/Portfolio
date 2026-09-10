"""Read native trace records with a real Spark session, entirely offline."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pyspark.sql import SparkSession
from receipt_langsmith.spark import (
    label_validation_viz_cache_helpers as helpers,
)


@pytest.mark.skipif(
    not os.environ.get("JAVA_HOME"), reason="Requires a local JDK"
)
def test_native_receipt_cache_matches_archived_parquet(tmp_path: Path) -> None:
    spark = (
        SparkSession.builder.master("local[1]")
        .appName("native-trace-test")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    try:
        root = {
            "schema_version": 1,
            "id": "root",
            "trace_id": "trace",
            "parent_run_id": None,
            "name": "receipt_processing",
            "run_type": "chain",
            "status": "success",
            "start_time": "2026-09-10T12:00:00+00:00",
            "end_time": "2026-09-10T12:00:02+00:00",
            "inputs": "{}",
            "outputs": json.dumps(
                {"success": True, "merchant_name": "Market"}
            ),
            "extra": json.dumps(
                {"metadata": {"image_id": "image", "receipt_id": 1}}
            ),
        }
        child = {
            **root,
            "id": "word",
            "parent_run_id": "root",
            "name": "llm_batch_validation",
            "start_time": "2026-09-10T12:00:01+00:00",
            "end_time": "2026-09-10T12:00:08+00:00",
            "outputs": json.dumps(
                {
                    "validations": [
                        {
                            "line_id": 1,
                            "word_id": 1,
                            "word_text": "Milk",
                            "final_label": "PRODUCT_NAME",
                            "decision": "VALID",
                            "confidence": "high",
                        }
                    ],
                }
            ),
        }
        path = tmp_path / "native-traces" / "date=2026-09-10"
        path.mkdir(parents=True)
        (path / "trace.ndjson").write_text(
            "\n".join(
                json.dumps(row)
                for row in [
                    child,
                    root,
                    {
                        **child,
                        "id": "failed-attempt",
                        "capture_status": "error",
                        "end_time": "2026-09-10T12:00:20+00:00",
                    },
                ]
            )
        )
        native = helpers.read_traces(spark, str(path.parent), "native")
        roots = helpers.extract_receipt_traces(native)
        assert roots[0]["duration_ms"] == 8000
        assert roots[0]["image_id"] == "image"
        validations = helpers.extract_validation_traces(native, ["trace"])
        assert len(validations["trace"]) == 1
        # Archive compatibility: identical normalized spans produce the same
        # public receipt payload through the unchanged cache assembly logic.
        parquet = str(tmp_path / "archive")
        native.write.parquet(parquet)
        archived = helpers.read_traces(spark, parquet)
        assert helpers.extract_receipt_traces(archived) == roots
        assert (
            helpers.extract_validation_traces(archived, ["trace"])
            == validations
        )
        lookup = {
            ("image", 1): {
                "cdn_s3_key": "receipt.jpg",
                "width": 100,
                "height": 200,
                "words": [
                    {"line_id": 1, "word_id": 1, "text": "Milk", "bbox": {}}
                ],
                "labels": {"1_1": "PRODUCT_NAME"},
            }
        }
        native_receipt = helpers.build_viz_receipt(
            roots[0], validations["trace"], lookup
        )
        archived_receipt = helpers.build_viz_receipt(
            helpers.extract_receipt_traces(archived)[0],
            helpers.extract_validation_traces(archived, ["trace"])["trace"],
            lookup,
        )
        assert native_receipt == archived_receipt
        assert native_receipt["words"][0]["decision"] == "VALID"
        assert native_receipt["merchant_name"] == "Market"
    finally:
        spark.stop()


def test_label_cache_write_failure_keeps_previous_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise helpers.ClientError(
            {"Error": {"Code": "AccessDenied"}}, "PutObject"
        )

    published = []
    monkeypatch.setattr(helpers, "write_receipt_json", fail)
    monkeypatch.setattr(
        helpers,
        "write_receipt_cache_index",
        lambda *args: published.append(args),
    )
    with pytest.raises(RuntimeError, match="Failed to upload"):
        helpers.write_cache(
            None,
            "cache",
            [{"image_id": "image", "receipt_id": 1}],
            "native-traces/",
        )
    assert published == []
