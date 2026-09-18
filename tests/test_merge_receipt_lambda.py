"""Offline merge cleanup contracts; AWS persistence is exercised with moto."""

import ast
import importlib.util
import io
import json
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

# isort: off
# Keep package grouping stable across CI's editable package environments.
import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from PIL import Image as PILImage
import receipt_dynamo
import receipt_upload.combine as combine
import receipt_upload.utils as upload_utils
from receipt_agent.lifecycle import receipt_manager
from receipt_dynamo import (
    DynamoClient,
    Image,
    Receipt,
    ReceiptLine,
    ReceiptWord,
)
from receipt_dynamo.entities.receipt_barcode import ReceiptBarcode
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo.entities.receipt_summary_record import ReceiptSummaryRecord
from receipt_dynamo_stream.message_builder import build_messages_from_records
from infra.receipt_line_item_updater import line_item_processor
from receipt_dynamo.entities.entity_mixins import CDNFieldsMixin
from receipt_dynamo.data.shared_exceptions import DynamoDBThroughputError
from receipt_upload.merchant_resolution import dynamo_embedding_write
from infra.receipt_summary_updater import summary_processor

# isort: on

SPEC = importlib.util.spec_from_file_location(
    "merge_handler_under_test",
    Path(__file__).resolve().parents[1]
    / "infra/merge_receipt_lambda/lambdas/merge_receipt.py",
)
merge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge)
IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
EVENT = {"image_id": IMAGE_ID, "receipt_ids": [1, 2]}
CDN_KEYS = [
    f.name for f in fields(CDNFieldsMixin) if f.name.endswith("s3_key")
]


@pytest.fixture(autouse=True)
def mock_aws_services(monkeypatch):
    """Replace the root AWS stub with moto, with no real credentials used."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws():
        yield


def receipt(rid):
    return Receipt(
        image_id=IMAGE_ID,
        receipt_id=rid,
        width=10,
        height=10,
        timestamp_added="2026-09-06T00:00:00+00:00",
        raw_s3_bucket="merge-raw",
        raw_s3_key=f"source/{rid}.png",
        cdn_s3_bucket="merge-site",
        top_left={"x": 0, "y": 1},
        top_right={"x": 1, "y": 1},
        bottom_left={"x": 0, "y": 0},
        bottom_right={"x": 1, "y": 0},
        **{name: f"source/{rid}/{name}" for name in CDN_KEYS},
    )


@pytest.fixture
def db():
    dynamo = boto3.client("dynamodb")
    # GSI3 is used by get_receipts_from_image. Geometry reads are stubbed.
    attrs = ["PK", "SK", "GSI3PK", "GSI3SK", "GSI4PK", "GSI4SK"]
    dynamo.create_table(
        TableName="merge-test",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"} for name in attrs
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": index,
                "KeySchema": [
                    {"AttributeName": f"{index}PK", "KeyType": "HASH"},
                    {"AttributeName": f"{index}SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for index in ("GSI3", "GSI4")
        ],
    )
    return DynamoClient("merge-test")


def put_child(db, rid, suffix, **attrs):
    db._client.put_item(
        TableName=db.table_name,
        Item={
            "PK": {"S": f"IMAGE#{IMAGE_ID}"},
            "SK": {"S": f"RECEIPT#{rid:05d}#{suffix}"},
            **attrs,
        },
    )


def keys(db):
    return {
        row["SK"]["S"]
        for row in db._client.scan(TableName=db.table_name)["Items"]
    }


def test_parent_deleter_and_paginated_child_purge(db, monkeypatch):
    for rid in (1, 2, 10000, 100000):
        db.add_receipt(receipt(rid))
        put_child(db, rid, "SUMMARY")
    suffixes = [
        "LINE#00001",
        "LINE#00001#WORD#00001",
        "LETTER#1",
        "LABEL#1",
        "TAG#1",
        "SECTION#1",
        "ROW#1",
        "LINE_ITEM#1",
        "SUMMARY",
        "PLACE",
        "METADATA",
        "FUTURE_CHILD_TYPE",
        "LINE#00001#EMBEDDING",
    ]
    for suffix in suffixes:
        put_child(db, 1, suffix)
    # Force multiple 1 MB query pages, plus multiple batches of 25 deletes.
    for i in range(60):
        put_child(db, 1, f"WORD#{i:05d}", payload={"S": "x" * 40000})
    assert receipt_manager.delete_receipt(db, IMAGE_ID, 1).success
    # The lifecycle method really deletes only the parent.
    assert (
        db._client.get_item(TableName=db.table_name, Key=receipt(1).key).get(
            "Item"
        )
        is None
    )
    assert db._client.get_item(
        TableName=db.table_name,
        Key={
            "PK": {"S": f"IMAGE#{IMAGE_ID}"},
            "SK": {"S": "RECEIPT#00001#SUMMARY"},
        },
    ).get("Item")
    query = Mock(wraps=db._client.query)
    monkeypatch.setattr(db._client, "query", query)
    assert merge._purge_receipt_children(db, IMAGE_ID, 1) == len(suffixes) + 60
    assert query.call_count >= 2
    assert all(call.kwargs["ConsistentRead"] for call in query.call_args_list)
    assert keys(db) == {
        f"RECEIPT#{rid:05d}{suffix}"
        for rid in (2, 10000, 100000)
        for suffix in ("", "#SUMMARY")
    }
    # Delimiter, not width alone, prevents 10000 from matching 100000.
    assert merge._purge_receipt_children(db, IMAGE_ID, 10000) == 1
    assert "RECEIPT#10000" in keys(db)  # helper never deletes a parent
    assert "RECEIPT#100000#SUMMARY" in keys(db)
    assert merge._purge_receipt_children(db, IMAGE_ID, 1) == 0


@pytest.fixture
def merge_env(db, monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", db.table_name)
    monkeypatch.setenv("RAW_BUCKET", "merge-raw")
    monkeypatch.setenv("SITE_BUCKET", "merge-site")
    s3 = boto3.client("s3")
    for bucket in ("merge-raw", "merge-site", "merge-originals"):
        s3.create_bucket(Bucket=bucket)
    image = PILImage.new("RGB", (10, 10), "white")
    png = io.BytesIO()
    image.save(png, format="PNG")
    s3.put_object(
        Bucket="merge-originals", Key="original.png", Body=png.getvalue()
    )
    for rid in (1, 2, 3):
        source = receipt(rid)
        db.add_receipt(source)
        put_child(db, rid, "SUMMARY")
        put_child(db, rid, "UNKNOWN")
        for bucket, key in merge._collect_receipt_assets(source):
            s3.put_object(Bucket=bucket, Key=key, Body=b"crop")
    sqs = boto3.client("sqs")
    queues = {}
    for env in ("SUMMARY_QUEUE_URL", "LINE_ITEM_QUEUE_URL"):
        queues[env] = sqs.create_queue(QueueName=env)["QueueUrl"]
        monkeypatch.setenv(env, queues[env])
    monkeypatch.setattr(receipt_dynamo, "DynamoClient", lambda table_name: db)
    db.add_image(
        Image(
            image_id=IMAGE_ID,
            width=10,
            height=10,
            timestamp_added="2026-09-10T00:00:00+00:00",
            raw_s3_bucket="merge-originals",
            raw_s3_key="original.png",
            receipt_count=3,
        )
    )
    monkeypatch.setattr(
        combine,
        "combine_receipt_words_to_image_coords",
        lambda *args, **kwargs: [object()],
    )
    monkeypatch.setattr(
        combine,
        "calculate_min_area_rect",
        lambda *args: dict(
            bounds={}, src_corners=[], warped_width=10, warped_height=10
        ),
    )
    monkeypatch.setattr(
        combine, "create_warped_receipt_image", lambda *args: image
    )
    monkeypatch.setattr(
        combine,
        "create_combined_receipt_records",
        lambda **kwargs: dict(
            receipt=receipt(4),
            receipt_lines=[],
            receipt_words=[],
            line_id_map={},
            section_line_id_map={},
            word_id_map={},
        ),
    )
    monkeypatch.setattr(
        combine,
        "combine_receipt_letters_to_image_coords",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        combine, "migrate_receipt_word_labels", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        combine, "get_best_receipt_place", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        upload_utils,
        "upload_png_to_s3",
        lambda image, bucket, key: s3.put_object(
            Bucket=bucket, Key=key, Body=png.getvalue()
        ),
    )
    monkeypatch.setattr(
        upload_utils, "upload_all_cdn_formats", lambda *args, **kwargs: {}
    )
    native = Mock(return_value={"written": 1, "failed": 0})
    monkeypatch.setattr(
        dynamo_embedding_write, "write_native_embeddings", native
    )
    return SimpleNamespace(db=db, s3=s3, sqs=sqs, queues=queues, native=native)


def queue_messages(env, queue):
    return env.sqs.receive_message(
        QueueUrl=env.queues[queue], MaxNumberOfMessages=10
    ).get("Messages", [])


def test_merge_collects_before_deletion_and_cleans_after(
    merge_env, monkeypatch
):
    env = merge_env
    collect = merge._collect_receipt_assets
    delete = receipt_manager.delete_receipt
    observed = []

    def capture(source):
        assert env.native.call_count == 1
        rid = getattr(source, "receipt_id", None)
        if rid in EVENT["receipt_ids"] and ("delete", rid) not in observed:
            assert env.db.get_receipt(IMAGE_ID, rid)
            observed.append(("collect", rid))
        return collect(source)

    def delete_parent(client, image_id, rid):
        assert ("collect", rid) in observed
        # All 13 references still exist at parent deletion time.
        for bucket, key in collect(receipt(rid)):
            env.s3.head_object(Bucket=bucket, Key=key)
        result = delete(client, image_id, rid)
        observed.append(("delete", rid))
        return result

    delete_assets = merge._delete_receipt_assets

    def delete_assets_after_parent(s3_client, assets):
        rid = [rid for action, rid in observed if action == "delete"][-1]
        assert "Item" not in env.db._client.get_item(
            TableName=env.db.table_name, Key=receipt(rid).key
        )
        delete_assets(s3_client, assets)

    monkeypatch.setattr(merge, "_collect_receipt_assets", capture)
    monkeypatch.setattr(
        merge, "_delete_receipt_assets", delete_assets_after_parent
    )
    monkeypatch.setattr(receipt_manager, "delete_receipt", delete_parent)
    result = merge.handler(EVENT, None)
    assert result["status"] == "success"
    assert result["deleted_receipts"] == [2, 1]
    assert result["new_receipt_id"] == 4
    assert [rid for action, rid in observed if action == "delete"] == [2, 1]
    for rid in (1, 2):
        assert observed.index(("collect", rid)) < observed.index(
            ("delete", rid)
        )
    assert not any(
        k.startswith(("RECEIPT#00001", "RECEIPT#00002")) for k in keys(env.db)
    )
    for bucket in ("merge-raw", "merge-site"):
        remaining = {
            obj["Key"]
            for obj in env.s3.list_objects_v2(Bucket=bucket).get(
                "Contents", []
            )
        }
        assert not any(
            key.startswith(("source/1", "source/2")) for key in remaining
        )
        assert {
            key for b, key in collect(receipt(3)) if b == bucket
        } <= remaining
    env.s3.head_object(Bucket="merge-originals", Key="original.png")
    for queue in env.queues:
        messages = queue_messages(env, queue)
        assert len(messages) == 1
        assert json.loads(messages[0]["Body"]) == {
            "entity_data": {"image_id": IMAGE_ID, "receipt_id": 4}
        }
    assert env.db.get_image(IMAGE_ID).receipt_count == 2


def test_dry_run_does_not_send_or_delete(merge_env, monkeypatch):
    env = merge_env
    before = keys(env.db)
    collect = Mock(side_effect=AssertionError("cleanup ran during dry run"))
    monkeypatch.setattr(merge, "_collect_receipt_assets", collect)
    assert (
        merge.handler({**EVENT, "dry_run": True}, None)["status"] == "dry_run"
    )
    assert keys(env.db) == before
    collect.assert_not_called()
    env.native.assert_not_called()
    for queue in env.queues:
        assert not queue_messages(env, queue)
    assert env.s3.list_objects_v2(Bucket="merge-site")["KeyCount"] == 36
    assert env.s3.list_objects_v2(Bucket="merge-raw")["KeyCount"] == 3


@pytest.mark.parametrize(
    "step", ["collect", "purge", "s3", "summary_queue", "line_item_queue"]
)
def test_cleanup_failures_remain_retryable(
    merge_env, monkeypatch, caplog, step
):
    env = merge_env
    if step in ("collect", "purge"):
        method = (
            "_collect_receipt_assets"
            if step == "collect"
            else "_purge_receipt_children"
        )
        monkeypatch.setattr(
            merge,
            method,
            Mock(side_effect=RuntimeError("injected cleanup failure")),
        )
    elif step == "s3":
        # One denied bucket must not suppress deletion from the other bucket.
        original_client = merge.boto3.client
        failing_s3 = original_client("s3")
        delete = failing_s3.delete_object

        def fail_raw(**kwargs):
            if kwargs["Bucket"] == "merge-raw":
                raise RuntimeError("injected cleanup failure")
            return delete(**kwargs)

        monkeypatch.setattr(failing_s3, "delete_object", fail_raw)
        monkeypatch.setattr(
            merge.boto3,
            "client",
            lambda service, **kwargs: (
                failing_s3
                if service == "s3"
                else original_client(service, **kwargs)
            ),
        )
    else:
        env_name = (
            "SUMMARY_QUEUE_URL"
            if step == "summary_queue"
            else "LINE_ITEM_QUEUE_URL"
        )
        env.sqs.delete_queue(QueueUrl=env.queues[env_name])
    result = merge.handler(EVENT, None)
    assert result["status"] == "error"
    assert env.db.get_receipt_merge(IMAGE_ID, [1, 2]).status != "COMPLETED"
    assert "Error merging receipts" in caplog.text
    for queue in env.queues:
        if step == "summary_queue" and queue == "SUMMARY_QUEUE_URL":
            continue
        if step == "line_item_queue" and queue == "LINE_ITEM_QUEUE_URL":
            continue
        messages = queue_messages(env, queue)
        assert len(messages) == int(
            step in ("summary_queue", "line_item_queue")
        )
    if step == "s3":
        # All objects for the interrupted source are attempted before failure.
        assert env.s3.list_objects_v2(Bucket="merge-site")["KeyCount"] == 24


def test_failed_parent_delete_retains_children_and_assets(
    merge_env, monkeypatch
):
    env = merge_env
    monkeypatch.setattr(
        receipt_manager,
        "delete_receipt",
        lambda *args: receipt_manager.ReceiptDeletionResult(
            receipt_id=args[2], success=False, error="denied"
        ),
    )
    result = merge.handler(EVENT, None)
    assert result["status"] == "error"
    assert "Failed to delete receipt" in result["error"]
    assert "RECEIPT#00001#UNKNOWN" in keys(env.db)
    assert env.s3.list_objects_v2(Bucket="merge-site")["KeyCount"] == 36


def test_native_failure_aborts_before_cleanup(merge_env):
    env = merge_env
    env.native.return_value = {"failed": 1}
    result = merge.handler(EVENT, None)
    assert result["status"] == "error"
    assert "source receipts NOT deleted" in result["error"]
    assert "RECEIPT#00001" in keys(env.db)
    for queue in env.queues:
        assert not queue_messages(env, queue)
    assert env.s3.list_objects_v2(Bucket="merge-site")["KeyCount"] == 36


def test_structured_exception_contract(merge_env, monkeypatch):
    monkeypatch.setattr(
        combine,
        "calculate_min_area_rect",
        Mock(side_effect=RuntimeError("geometry failed")),
    )
    assert merge.handler(EVENT, None) == {
        **EVENT,
        "status": "error",
        "error": "geometry failed",
    }
    assert merge.handler({}, None) == {
        "status": "error",
        "error": "Missing required field: image_id",
    }


@pytest.mark.parametrize("when", ["before_write", "after_write"])
def test_inflight_summary_cannot_survive_parent_deletion(
    db, monkeypatch, when
):
    db.add_receipt(receipt(1))
    monkeypatch.setattr(summary_processor, "dynamo_client", db)
    monkeypatch.setattr(
        db,
        "list_receipt_word_labels_for_receipt",
        lambda *args, **kwargs: ([], None),
    )
    for method in (
        "list_receipt_words_from_receipt",
        "list_receipt_lines_from_receipt",
        "get_receipt_sections_from_receipt",
        "get_receipt_line_items_from_receipt",
    ):
        monkeypatch.setattr(db, method, lambda *args, **kwargs: [])
    write = db.upsert_receipt_summary

    def race(record):
        if when == "after_write":
            write(record)
        assert receipt_manager.delete_receipt(db, IMAGE_ID, 1).success
        merge._purge_receipt_children(db, IMAGE_ID, 1)
        if when == "before_write":
            write(record)  # Reproduce a late summary AFTER the entire purge.
            assert "RECEIPT#00001#SUMMARY" in keys(db)

    monkeypatch.setattr(db, "upsert_receipt_summary", race)
    get = Mock(wraps=db._client.get_item)
    monkeypatch.setattr(db._client, "get_item", get)
    result = summary_processor.update_receipt_summary(IMAGE_ID, 1)
    assert result["skipped"] == "parent receipt deleted"
    assert keys(db) == set()
    assert any(
        call.kwargs.get("ConsistentRead") for call in get.call_args_list
    )
    # A later stream delivery also skips the deleted source.
    assert (
        summary_processor.update_receipt_summary(IMAGE_ID, 1)["skipped"]
        == "parent receipt deleted"
    )


def test_purge_retries_unprocessed_items_and_bounds_failures(db, monkeypatch):
    put_child(db, 1, "SUMMARY")
    real_write = db._client.batch_write_item
    requests = []

    def once_unprocessed(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return {"UnprocessedItems": kwargs["RequestItems"]}
        return real_write(**kwargs)

    monkeypatch.setattr(db._client, "batch_write_item", once_unprocessed)
    monkeypatch.setattr(
        "receipt_dynamo.data._receipt.time.sleep", lambda seconds: None
    )
    assert merge._purge_receipt_children(db, IMAGE_ID, 1) == 1
    assert len(requests) == 2
    put_child(db, 1, "SUMMARY")
    blocked = Mock(
        side_effect=lambda **kwargs: {
            "UnprocessedItems": kwargs["RequestItems"]
        }
    )
    monkeypatch.setattr(db._client, "batch_write_item", blocked)
    with pytest.raises(DynamoDBThroughputError, match="exhausted retries"):
        merge._purge_receipt_children(db, IMAGE_ID, 1)
    assert blocked.call_count == 5


def test_merge_iam_scopes_asset_deletes_and_queue_sends():
    """Evaluate only pure policy builders, without importing/running Pulumi."""
    path = (
        Path(__file__).resolve().parents[1]
        / "infra/merge_receipt_lambda/infrastructure.py"
    )
    tree = ast.parse(path.read_text())
    policies = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(
            node.targets[0], ast.Name
        ):
            name = node.targets[0].id
            if name not in ("s3_policy", "sqs_policy", "dynamodb_policy"):
                continue
            builder = next(
                n for n in ast.walk(node.value) if isinstance(n, ast.Lambda)
            )
            policies[name] = eval(
                compile(ast.Expression(builder), str(path), "eval"),
                {"json": json},
            )
    table_arn = "arn:aws:dynamodb:us-east-1:123456789012:table/merge-test"
    dynamo = json.loads(policies["dynamodb_policy"](table_arn))["Statement"]
    assert "dynamodb:ConditionCheckItem" in dynamo[0]["Action"]
    assert "dynamodb:TransactWriteItems" not in dynamo[0]["Action"]
    assert set(dynamo[0]["Resource"]) == {table_arn, f"{table_arn}/index/*"}
    s3 = policies["s3_policy"](["raw", "site", "originals"])
    statements = json.loads(s3)["Statement"]
    deletes = [s for s in statements if "s3:DeleteObject" in s["Action"]]
    assert len(deletes) == 1
    assert set(deletes[0]["Resource"]) == {
        "arn:aws:s3:::raw/*",
        "arn:aws:s3:::site/*",
    }
    arns = [
        "arn:aws:sqs:us-east-1:123456789012:summary",
        "arn:aws:sqs:us-east-1:123456789012:line-items",
    ]
    assert json.loads(policies["sqs_policy"](arns))["Statement"] == [
        {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": arns}
    ]


def geometry(x, y, width, height):
    return {
        "bounding_box": {"x": x, "y": y, "width": width, "height": height},
        "top_left": {"x": x, "y": y + height},
        "top_right": {"x": x + width, "y": y + height},
        "bottom_left": {"x": x, "y": y},
        "bottom_right": {"x": x + width, "y": y},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
        "confidence": 1.0,
    }


@pytest.fixture
def lifecycle_env(db, monkeypatch):
    """Real geometry and Dynamo entities; only external embeddings/CDN encoding stubbed."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", db.table_name)
    monkeypatch.setenv("RAW_BUCKET", "merge-raw")
    monkeypatch.setenv("SITE_BUCKET", "merge-site")
    monkeypatch.delenv("TRIGGER_REOCR_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("OCR_JOB_QUEUE_URL", raising=False)
    s3 = boto3.client("s3")
    for bucket in ("merge-raw", "merge-site"):
        s3.create_bucket(Bucket=bucket)
    png = io.BytesIO()
    PILImage.new("RGB", (600, 1200), "white").save(png, format="PNG")
    sources = {
        rid: replace(receipt(rid), width=600, height=1200) for rid in (1, 2, 3)
    }
    # One CDN object on source 2 is also owned by a surviving receipt.
    sources[3].cdn_avif_s3_key = sources[2].cdn_avif_s3_key
    for source in sources.values():
        db.add_receipt(source)
        for bucket, key in merge._collect_receipt_assets(source):
            s3.put_object(Bucket=bucket, Key=key, Body=png.getvalue())
    # Native receipt 1 and its Image share raw and every CDN object.
    image = Image(
        image_id=IMAGE_ID,
        width=600,
        height=1200,
        timestamp_added="2026-09-08T00:00:00+00:00",
        raw_s3_bucket=sources[1].raw_s3_bucket,
        raw_s3_key=sources[1].raw_s3_key,
        cdn_s3_bucket=sources[1].cdn_s3_bucket,
        receipt_count=3,
        **{name: getattr(sources[1], name) for name in CDN_KEYS},
    )
    db.add_image(image)
    for rid, name, price, y in (
        (1, "MILK", "3.00", 0.8),
        (2, "BREAD", "4.00", 0.6),
    ):
        db.add_receipt_line(
            ReceiptLine(
                image_id=IMAGE_ID,
                receipt_id=rid,
                line_id=1,
                text=f"{name} {price}",
                **geometry(0.2, y, 0.65, 0.02),
            )
        )
        for wid, text, x, width in (
            (1, name, 0.2, 0.2),
            (2, price, 0.72, 0.13),
        ):
            db.add_receipt_word(
                ReceiptWord(
                    image_id=IMAGE_ID,
                    receipt_id=rid,
                    line_id=1,
                    word_id=wid,
                    text=text,
                    **geometry(x, y, width, 0.02),
                )
            )
        db.add_receipt_section(
            ReceiptSection(
                image_id=IMAGE_ID,
                receipt_id=rid,
                section_type="ITEMS",
                line_ids=[1],
                row_ids=[1],
                created_at=datetime.now(timezone.utc),
                validation_status="VALID",
                model_source="swift-worker-v1",
            )
        )
        db.add_receipt_barcode(
            ReceiptBarcode(
                image_id=IMAGE_ID,
                receipt_id=rid,
                barcode_id=0,
                symbology="QR",
                text=f"offline-barcode-{rid}",
                **geometry(0.1, rid * 0.1, 0.8, 0.05),
            )
        )
        # Legacy children must disappear without matching receipt 10.
        db._client.put_item(
            TableName=db.table_name,
            Item={
                "PK": {"S": f"IMAGE#{IMAGE_ID}"},
                "SK": {"S": f"RECEIPT#{rid}#LEGACY_CHILD"},
            },
        )
    sqs = boto3.client("sqs")
    queues = {}
    for env in ("SUMMARY_QUEUE_URL", "LINE_ITEM_QUEUE_URL"):
        queues[env] = sqs.create_queue(QueueName=env)["QueueUrl"]
        monkeypatch.setenv(env, queues[env])
    monkeypatch.setattr(receipt_dynamo, "DynamoClient", lambda table_name: db)
    monkeypatch.setattr(summary_processor, "dynamo_client", db)
    monkeypatch.setattr(line_item_processor, "dynamo_client", db)
    native = Mock(return_value={"written": 6, "failed": 0})
    monkeypatch.setattr(
        dynamo_embedding_write, "write_native_embeddings", native
    )
    monkeypatch.setattr(
        upload_utils, "upload_all_cdn_formats", lambda *args, **kwargs: {}
    )
    return SimpleNamespace(
        db=db,
        s3=s3,
        sqs=sqs,
        queues=queues,
        image=image,
        sources=sources,
        native=native,
    )


def summary_stream(old, new):
    return build_messages_from_records(
        [
            {
                "eventName": "MODIFY" if old else "INSERT",
                "eventID": "offline-summary-event",
                "awsRegion": "us-east-1",
                "dynamodb": {
                    "Keys": new.key,
                    "NewImage": new.to_item(),
                    **({"OldImage": old.to_item()} if old else {}),
                },
            }
        ]
    )


@pytest.mark.parametrize(
    "ordering", ["summary-first", "items-first", "late-summary"]
)
def test_real_merge_preserves_metadata_assets_and_final_item_count(
    lifecycle_env, ordering
):
    env = lifecycle_env
    result = merge.handler(EVENT, None)
    assert result["status"] == "success", result
    assert result["sections_migrated"] == 1
    assert result["barcodes_migrated"] == 2
    assert result["new_receipt_id"] == 4
    for rid in (1, 2):
        assert not env.db.receipt_exists_consistent(IMAGE_ID, rid)
        assert not any(
            k.startswith((f"RECEIPT#{rid:05d}#", f"RECEIPT#{rid}#"))
            for k in keys(env.db)
        )
    sections = env.db.get_receipt_sections_from_receipt(IMAGE_ID, 4)
    assert sections[0].section_type == "ITEMS"
    assert sections[0].line_ids == [1, 2]
    assert sections[0].row_ids is None
    assert sections[0].validation_status == "PENDING"
    barcodes = env.db.list_receipt_barcodes_from_receipt_consistent(
        IMAGE_ID, 4
    )
    assert {b.text for b in barcodes} == {
        "offline-barcode-1",
        "offline-barcode-2",
    }
    assert {b.barcode_id for b in barcodes} == {0, 1}
    for barcode in barcodes:
        for corner in (
            barcode.top_left,
            barcode.top_right,
            barcode.bottom_left,
            barcode.bottom_right,
        ):
            assert -1e-8 <= corner["x"] <= 1 + 1e-8
            assert -1e-8 <= corner["y"] <= 1 + 1e-8
    for owner in (env.image, env.sources[3]):
        for bucket, key in merge._collect_receipt_assets(owner):
            env.s3.head_object(Bucket=bucket, Key=key)
    assert env.db.get_image(IMAGE_ID).receipt_count == 2
    with pytest.raises(ClientError, match="404"):
        env.s3.head_object(Bucket="merge-raw", Key=env.sources[2].raw_s3_key)
    for queue in env.queues:
        assert len(queue_messages(env, queue)) == 1

    if ordering == "items-first":
        assert (
            line_item_processor.update_receipt_line_items(IMAGE_ID, 4)["items"]
            == 2
        )
    summary_processor.update_receipt_summary(IMAGE_ID, 4)
    before = env.db.get_receipt_summary(IMAGE_ID, 4)
    assert (
        line_item_processor.update_receipt_line_items(IMAGE_ID, 4)["items"]
        == 2
    )
    after = env.db.get_receipt_summary(IMAGE_ID, 4)
    assert after.item_count == 2
    assert after.timestamp_computed == before.timestamp_computed
    assert summary_stream(before, after) == []  # No extraction loop.
    if ordering == "late-summary":
        stale = ReceiptSummaryRecord.from_summary(before.summary)
        env.db.upsert_receipt_summary(stale)
        assert len(summary_stream(after, stale)) == 1
        line_item_processor.update_receipt_line_items(IMAGE_ID, 4)
        assert env.db.get_receipt_summary(IMAGE_ID, 4).item_count == 2


@pytest.mark.parametrize(
    "method",
    [
        "add_receipt_sections",
        "add_receipt_barcodes",
        "get_receipt_details",
    ],
)
def test_metadata_failure_retains_sources(lifecycle_env, monkeypatch, method):
    env = lifecycle_env
    monkeypatch.setattr(
        env.db, method, Mock(side_effect=RuntimeError("metadata unavailable"))
    )
    result = merge.handler(EVENT, None)
    assert result["status"] == "error"
    for rid in (1, 2):
        assert env.db.receipt_exists_consistent(IMAGE_ID, rid)
        for bucket, key in merge._collect_receipt_assets(env.sources[rid]):
            env.s3.head_object(Bucket=bucket, Key=key)
    env.native.assert_not_called()


@pytest.mark.parametrize(
    "ordering", ["before-write", "after-write", "partial-write-error"]
)
def test_late_item_writer_cannot_resurrect_deleted_children(
    lifecycle_env, monkeypatch, ordering
):
    env = lifecycle_env
    write = env.db.add_receipt_line_items

    def race(items):
        if ordering == "after-write":
            write(items)
        env.db.delete_receipt(env.db.get_receipt(IMAGE_ID, 1))
        env.db.purge_receipt_children(IMAGE_ID, 1)
        if ordering != "after-write":
            write(items)
        if ordering == "partial-write-error":
            raise RuntimeError("interrupted batch")

    monkeypatch.setattr(env.db, "add_receipt_line_items", race)
    if ordering == "partial-write-error":
        with pytest.raises(RuntimeError, match="interrupted batch"):
            line_item_processor.update_receipt_line_items(IMAGE_ID, 1)
    else:
        assert (
            line_item_processor.update_receipt_line_items(IMAGE_ID, 1)[
                "reason"
            ]
            == "parent receipt deleted"
        )
    assert not any(
        k.startswith(("RECEIPT#00001#", "RECEIPT#1#")) for k in keys(env.db)
    )
    assert (
        line_item_processor.update_receipt_line_items(IMAGE_ID, 1)["reason"]
        == "parent receipt deleted"
    )


def test_deduplicated_words_keep_section_line_aliases(lifecycle_env):
    env = lifecycle_env
    # Every word of source 2 duplicates source 1. Only source 2 has a zone.
    source_words = env.db.list_receipt_words_from_receipt(IMAGE_ID, 1)
    for word in source_words:
        env.db.update_receipt_word(replace(word, receipt_id=2))
    zone = env.db.get_receipt_sections_from_receipt(IMAGE_ID, 1)[0]
    env.db.delete_receipt_section(
        zone.receipt_id, zone.image_id, zone.section_type
    )
    result = merge.handler(EVENT, None)
    assert result["status"] == "success", result
    assert result["words_merged"] == 2
    zones = env.db.get_receipt_sections_from_receipt(IMAGE_ID, 4)
    assert len(zones) == 1
    assert zones[0].line_ids == [1]
    assert (
        line_item_processor.update_receipt_line_items(IMAGE_ID, 4)["items"]
        == 1
    )


def test_unknown_asset_ownership_retains_source_objects(
    lifecycle_env, monkeypatch
):
    env = lifecycle_env
    monkeypatch.setattr(
        env.db,
        "get_receipts_from_image_consistent",
        Mock(side_effect=RuntimeError("ownership unavailable")),
    )
    result = merge.handler(EVENT, None)
    assert result["status"] == "error", result
    assert "ownership unavailable" in result["error"]
    for source in env.sources.values():
        for bucket, key in merge._collect_receipt_assets(source):
            env.s3.head_object(Bucket=bucket, Key=key)


@pytest.mark.parametrize("with_label", [False, True])
def test_empty_extraction_refreshes_count_and_preserves_label_fallback(
    lifecycle_env, with_label
):
    env = lifecycle_env
    summary_processor.update_receipt_summary(IMAGE_ID, 1)
    env.db.update_receipt_summary_item_count(IMAGE_ID, 1, 9)
    env.db.delete_receipt_section(1, IMAGE_ID, "ITEMS")
    if with_label:
        env.db.add_receipt_word_label(
            ReceiptWordLabel(
                image_id=IMAGE_ID,
                receipt_id=1,
                line_id=1,
                word_id=2,
                label="LINE_TOTAL",
                reasoning="Offline fixture",
                validation_status="VALID",
                timestamp_added=datetime.now(timezone.utc),
            )
        )
    assert (
        line_item_processor.update_receipt_line_items(IMAGE_ID, 1)["items"]
        == 0
    )
    assert env.db.get_receipt_summary(IMAGE_ID, 1).item_count == int(
        with_label
    )


def test_rotated_barcode_maps_to_image_and_back(lifecycle_env):
    env = lifecycle_env
    source = replace(
        env.sources[1],
        width=400,
        height=600,
        top_left={"x": 0.8, "y": 0.9},
        top_right={"x": 0.8, "y": 0.1},
        bottom_right={"x": 0.2, "y": 0.1},
        bottom_left={"x": 0.2, "y": 0.9},
    )
    env.db.update_receipt(source)
    original = env.db.get_receipt_barcode(IMAGE_ID, 1, 0)
    transformed = combine.receipt_barcodes_in_image_space(
        env.db, IMAGE_ID, [1], 600, 1200
    )[0]
    for name in ("top_left", "top_right", "bottom_left", "bottom_right"):
        point = getattr(original, name)
        u = point["x"] * 400 / 399
        v = (1 - point["y"]) * 600 / 599
        assert getattr(transformed, name) == pytest.approx(
            {"x": 0.8 - 0.6 * v, "y": 0.9 - 0.8 * u}
        )
    crop = [(480, 120), (480, 1080), (120, 1080), (120, 120)]
    restored = combine.migrate_receipt_barcodes(
        [transformed], 4, 600, 1200, crop, 400, 600
    )[0]
    for name in ("top_left", "top_right", "bottom_left", "bottom_right"):
        assert getattr(restored, name) == pytest.approx(
            getattr(original, name)
        )
    assert restored.text == original.text
    assert restored.symbology == original.symbology
    assert restored.confidence == original.confidence
    assert restored.angle_degrees == pytest.approx(0, abs=1e-8)
    assert env.db.get_receipt_barcode(IMAGE_ID, 1, 0) == original
