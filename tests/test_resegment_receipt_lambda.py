"""End-to-end local test for plan/apply receipt re-segmentation."""

import io
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import boto3
import pytest
import receipt_dynamo
import receipt_upload.utils
from moto import mock_aws
from PIL import Image as PILImage
from receipt_dynamo import (
    DynamoClient,
    Image,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
)

from infra.resegment_receipt_lambda.lambdas import resegment_receipt
from infra.resegment_receipt_lambda.lambdas.resegment_receipt import (
    _segment_geometry,
    _stage_outputs,
    apply_plan,
    create_plan,
    get_plan,
    revise_plan,
)


@pytest.fixture(autouse=True)
def mock_aws_services():
    """Override the root infrastructure stub so moto owns boto3 here."""
    yield


def _create_table(table_name: str) -> None:
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
            {"AttributeName": "GSI3PK", "AttributeType": "S"},
            {"AttributeName": "GSI3SK", "AttributeType": "S"},
            {"AttributeName": "GSI4PK", "AttributeType": "S"},
            {"AttributeName": "GSI4SK", "AttributeType": "S"},
            {"AttributeName": "TYPE", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": name,
                "KeySchema": [
                    {"AttributeName": pk, "KeyType": "HASH"},
                    {"AttributeName": sk, "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for name, pk, sk in (
                ("GSI1", "GSI1PK", "GSI1SK"),
                ("GSI2", "GSI2PK", "GSI2SK"),
                ("GSI3", "GSI3PK", "GSI3SK"),
                ("GSI4", "GSI4PK", "GSI4SK"),
            )
        ]
        + [
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _geometry(x1: float, x2: float, y1: float, y2: float) -> dict:
    return {
        "bounding_box": {
            "x": x1,
            "y": y2,
            "width": x2 - x1,
            "height": y1 - y2,
        },
        "top_left": {"x": x1, "y": y1},
        "top_right": {"x": x2, "y": y1},
        "bottom_left": {"x": x1, "y": y2},
        "bottom_right": {"x": x2, "y": y2},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
        "confidence": 1.0,
    }


def test_handler_does_not_expose_test_only_apply_controls(monkeypatch):
    captured = {}

    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "table")
    monkeypatch.setenv("RAW_BUCKET", "raw")
    monkeypatch.setenv("SITE_BUCKET", "site")
    monkeypatch.setenv("CHROMADB_BUCKET", "chroma")
    monkeypatch.setattr(receipt_dynamo, "DynamoClient", lambda table_name: object())
    monkeypatch.setattr(resegment_receipt.boto3, "client", lambda service: object())

    def fake_apply(event, **kwargs):
        del kwargs
        captured.update(event)
        return {"status": "APPLIED"}

    monkeypatch.setattr(resegment_receipt, "apply_plan", fake_apply)

    result = resegment_receipt.handler(
        {
            "mode": "apply",
            "plan_id": "plan-1",
            "plan_hash": "hash-1",
            "create_embeddings": False,
            "wait_for_embeddings": False,
        },
        None,
    )

    assert result == {"status": "APPLIED"}
    assert captured == {"plan_id": "plan-1", "plan_hash": "hash-1"}


def test_segment_geometry_records_requested_padding_at_image_edge():
    word = {
        "top_left": {"x": 0.0, "y": 20.0},
        "top_right": {"x": 10.0, "y": 20.0},
        "bottom_left": {"x": 0.0, "y": 10.0},
        "bottom_right": {"x": 10.0, "y": 10.0},
    }

    geometry = _segment_geometry([word], 100, 100, padding_px=2)

    assert geometry["requested_padding_px"] == 2
    assert geometry["applied_padding_px"] == 2
    assert geometry["warped_width"] == 14
    assert geometry["warped_height"] == 14


def test_stage_outputs_tracks_partial_cdn_uploads_for_rollback(monkeypatch):
    monkeypatch.setattr(receipt_upload.utils, "upload_png_to_s3", lambda *args: None)

    def fail_mid_upload(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("simulated CDN failure")

    monkeypatch.setattr(
        receipt_upload.utils,
        "upload_all_cdn_formats",
        fail_mid_upload,
    )
    uploaded_keys = []
    output = {
        "image": PILImage.new("RGB", (10, 10), "white"),
        "raw_key": "receipts/img/img_RECEIPT_00002.png",
        "receipt": SimpleNamespace(image_id="img", receipt_id=2),
    }

    with pytest.raises(RuntimeError, match="simulated CDN failure"):
        _stage_outputs(
            outputs=[output],
            dynamo_client=object(),
            raw_bucket="raw",
            site_bucket="site",
            uploaded_keys=uploaded_keys,
        )

    assert output["raw_key"] in uploaded_keys
    assert "assets/img/2_thumbnail.jpg" in uploaded_keys
    assert "assets/img/2.avif" in uploaded_keys
    assert len(uploaded_keys) == 13


@mock_aws
def test_plan_and_apply_split_is_idempotent_and_preserves_labels():
    table_name = "ReceiptResegmentTest"
    raw_bucket = "resegment-raw"
    site_bucket = "resegment-site"
    image_bucket = "resegment-images"
    chromadb_bucket = "resegment-chroma"
    _create_table(table_name)
    s3_client = boto3.client("s3", region_name="us-east-1")
    for bucket in (raw_bucket, site_bucket, image_bucket, chromadb_bucket):
        s3_client.create_bucket(Bucket=bucket)

    image_id = str(uuid4())
    timestamp = datetime.now(timezone.utc)
    original = PILImage.new("RGB", (200, 300), "white")
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    s3_client.put_object(
        Bucket=image_bucket,
        Key="original.png",
        Body=buffer.getvalue(),
    )

    client = DynamoClient(table_name)
    client.add_image(
        Image(
            image_id=image_id,
            width=200,
            height=300,
            timestamp_added=timestamp,
            raw_s3_bucket=image_bucket,
            raw_s3_key="original.png",
            receipt_count=1,
        )
    )
    client.add_receipt(
        Receipt(
            image_id=image_id,
            receipt_id=1,
            width=200,
            height=300,
            timestamp_added=timestamp,
            raw_s3_bucket=image_bucket,
            raw_s3_key="original.png",
            top_left={"x": 0.0, "y": 1.0},
            top_right={"x": 1.0, "y": 1.0},
            bottom_left={"x": 0.0, "y": 0.0},
            bottom_right={"x": 1.0, "y": 0.0},
        )
    )
    line_geometry = [
        _geometry(0.1, 0.35, 0.8, 0.7),
        _geometry(0.6, 0.9, 0.4, 0.3),
    ]
    lines = [
        ReceiptLine(
            image_id=image_id,
            receipt_id=1,
            line_id=index,
            text=text,
            **geometry,
        )
        for index, (text, geometry) in enumerate(
            zip(("LEFT", "RIGHT"), line_geometry), start=1
        )
    ]
    words = [
        ReceiptWord(
            image_id=image_id,
            receipt_id=1,
            line_id=index,
            word_id=1,
            text=text,
            **geometry,
        )
        for index, (text, geometry) in enumerate(
            zip(("LEFT", "RIGHT"), line_geometry), start=1
        )
    ]
    labels = [
        ReceiptWordLabel(
            image_id=image_id,
            receipt_id=1,
            line_id=index,
            word_id=1,
            label="MERCHANT_NAME" if index == 1 else "GRAND_TOTAL",
            reasoning="test label",
            timestamp_added=timestamp,
            validation_status="VALID",
            label_proposed_by="test",
        )
        for index in (1, 2)
    ]
    client.add_receipt_lines(lines)
    client.add_receipt_words(words)
    client.add_receipt_word_labels(labels)

    plan = create_plan(
        {
            "image_id": image_id,
            "source_receipt_id": 1,
            "padding_px": 2,
            "segments": [
                {"segment_key": "left", "include_line_ids": [1]},
                {"segment_key": "right", "include_line_ids": [2]},
            ],
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )
    assert plan["totals"]["source_words"] == 2
    assert plan["image"]["image_type"] == "SCAN"
    assert plan["visualization"]["effective_strategy"] == "RECTANGULAR"
    assert plan["applicable"] is True
    assert set(plan["preview_urls"]) == {"left", "right"}

    result = apply_plan(
        {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "create_embeddings": False,
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
        site_bucket=site_bucket,
        chromadb_bucket=chromadb_bucket,
    )

    assert result["status"] == "APPLIED"
    assert result["output_receipt_ids"] == [2, 3]
    assert client.get_receipt_item_type_counts(image_id, 1) == {}
    for receipt_id in (2, 3):
        details = client.get_receipt_details(image_id, receipt_id)
        assert len(details.words) == 1
        assert len(details.labels) == 1
        assert details.labels[0].validation_status == "VALID"
        assert details.labels[0].label_proposed_by == "test"

    repeated = apply_plan(
        {"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]},
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
        site_bucket=site_bucket,
        chromadb_bucket=chromadb_bucket,
    )
    assert repeated == result


@mock_aws
def test_photo_v2_plan_visualizes_revises_and_blocks_layered_apply():
    table_name = "ReceiptResegmentV2Test"
    raw_bucket = "resegment-v2-raw"
    site_bucket = "resegment-v2-site"
    image_bucket = "resegment-v2-images"
    chromadb_bucket = "resegment-v2-chroma"
    _create_table(table_name)
    s3_client = boto3.client("s3", region_name="us-east-1")
    for bucket in (raw_bucket, site_bucket, image_bucket, chromadb_bucket):
        s3_client.create_bucket(Bucket=bucket)

    image_id = str(uuid4())
    timestamp = datetime.now(timezone.utc)
    original = PILImage.new("RGB", (200, 300), "white")
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    s3_client.put_object(
        Bucket=image_bucket,
        Key="stacked-photo.png",
        Body=buffer.getvalue(),
    )

    client = DynamoClient(table_name)
    client.add_image(
        Image(
            image_id=image_id,
            width=200,
            height=300,
            timestamp_added=timestamp,
            raw_s3_bucket=image_bucket,
            raw_s3_key="stacked-photo.png",
            image_type="PHOTO",
            receipt_count=1,
        )
    )
    client.add_receipt(
        Receipt(
            image_id=image_id,
            receipt_id=1,
            width=200,
            height=300,
            timestamp_added=timestamp,
            raw_s3_bucket=image_bucket,
            raw_s3_key="stacked-photo.png",
            top_left={"x": 0.0, "y": 1.0},
            top_right={"x": 1.0, "y": 1.0},
            bottom_left={"x": 0.0, "y": 0.0},
            bottom_right={"x": 1.0, "y": 0.0},
        )
    )
    geometries = [
        _geometry(0.1, 0.35, 0.82, 0.72),
        _geometry(0.6, 0.9, 0.42, 0.32),
    ]
    client.add_receipt_lines(
        [
            ReceiptLine(
                image_id=image_id,
                receipt_id=1,
                line_id=index,
                text=text,
                **geometry,
            )
            for index, (text, geometry) in enumerate(
                zip(("CARD", "GUEST"), geometries), start=1
            )
        ]
    )
    client.add_receipt_words(
        [
            ReceiptWord(
                image_id=image_id,
                receipt_id=1,
                line_id=index,
                word_id=1,
                text=text,
                **geometry,
            )
            for index, (text, geometry) in enumerate(
                zip(("CARD", "GUEST"), geometries), start=1
            )
        ]
    )

    segments = [
        {
            "segment_key": "card",
            "z_index": 0,
            "occluded_by": ["guest"],
            "visible_regions": [
                {
                    "region_id": "card-visible",
                    "points": [
                        {"x": 0.0, "y": 0.55},
                        {"x": 0.48, "y": 0.55},
                        {"x": 0.48, "y": 1.0},
                        {"x": 0.0, "y": 1.0},
                    ],
                }
            ],
        },
        {
            "segment_key": "guest",
            "z_index": 1,
            "visible_regions": [
                {
                    "region_id": "guest-visible",
                    "points": [
                        {"x": 0.5, "y": 0.0},
                        {"x": 1.0, "y": 0.0},
                        {"x": 1.0, "y": 0.5},
                        {"x": 0.5, "y": 0.5},
                    ],
                }
            ],
        },
    ]
    assignments = {
        "lines": [
            {"line_id": 1, "segment_key": "card"},
            {"line_id": 2, "segment_key": "guest"},
        ]
    }
    plan = create_plan(
        {
            "schema_version": 2,
            "image_id": image_id,
            "source_receipt_id": 1,
            "segments": segments,
            "assignments": assignments,
            "visualization": {"strategy": "LAYERED_MULTI_REGION"},
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )

    assert plan["image"]["image_type"] == "PHOTO"
    assert plan["revision"] == 1
    assert plan["applicable"] is False
    assert plan["preview_urls"]["contact_sheet"]
    assert set(plan["visualizations"]["segments"]) == {"card", "guest"}
    assert {finding["code"] for finding in plan["findings"]} >= {
        "LAYERED_APPLY_NOT_SUPPORTED"
    }
    for artifact_name in ("overlay", "contact_sheet"):
        artifact = plan["visualizations"][artifact_name]
        stored = s3_client.get_object(Bucket=raw_bucket, Key=artifact["s3_key"])
        assert stored["Body"].read()

    fetched = get_plan(
        {"plan_id": plan["plan_id"]},
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )
    assert fetched["is_latest"] is True
    assert fetched["preview_urls"]["overlay"]

    revised_segments = [{**segment} for segment in segments]
    revised_segments[0]["visible_regions"] = [
        {
            "region_id": "card-visible-revised",
            "points": [
                {"x": 0.0, "y": 0.6},
                {"x": 0.48, "y": 0.6},
                {"x": 0.48, "y": 1.0},
                {"x": 0.0, "y": 1.0},
            ],
        }
    ]
    revised = revise_plan(
        {
            "plan_id": plan["plan_id"],
            "base_revision": 1,
            "base_plan_hash": plan["plan_hash"],
            "segments": revised_segments,
            "assignments": assignments,
            "visualization": {"strategy": "LAYERED_MULTI_REGION"},
            "revision_reason": "Tighten the visible card region",
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )
    assert revised["revision"] == 2
    assert revised["supersedes_plan_hash"] == plan["plan_hash"]
    old = get_plan(
        {"plan_id": plan["plan_id"], "revision": 1},
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )
    assert old["is_latest"] is False
    assert old["applicable"] is False

    with pytest.raises(ValueError, match="base_revision is stale"):
        revise_plan(
            {
                "plan_id": plan["plan_id"],
                "base_revision": 1,
                "base_plan_hash": plan["plan_hash"],
                "segments": segments,
                "assignments": assignments,
                "revision_reason": "Stale concurrent edit",
            },
            dynamo_client=client,
            s3_client=s3_client,
            raw_bucket=raw_bucket,
        )

    with pytest.raises(ValueError, match="plan_hash does not match"):
        apply_plan(
            {"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]},
            dynamo_client=client,
            s3_client=s3_client,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            chromadb_bucket=chromadb_bucket,
        )

    with pytest.raises(ValueError, match="blocking findings"):
        apply_plan(
            {"plan_id": revised["plan_id"], "plan_hash": revised["plan_hash"]},
            dynamo_client=client,
            s3_client=s3_client,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            chromadb_bucket=chromadb_bucket,
        )

    scan_image = client.get_image(image_id)
    scan_image.image_type = "SCAN"
    client.update_image(scan_image)
    with pytest.raises(ValueError, match="confirm_stacked_scan=true"):
        create_plan(
            {
                "schema_version": 2,
                "image_id": image_id,
                "source_receipt_id": 1,
                "segments": segments,
                "assignments": assignments,
                "visualization": {"strategy": "LAYERED_MULTI_REGION"},
            },
            dynamo_client=client,
            s3_client=s3_client,
            raw_bucket=raw_bucket,
        )


def _seed_two_line_receipt(table_name, raw_bucket, image_bucket):
    """Create the table, buckets, image, and a two-line source receipt."""
    _create_table(table_name)
    s3_client = boto3.client("s3", region_name="us-east-1")
    for bucket in (raw_bucket, "resegment-site", image_bucket, "resegment-chroma"):
        s3_client.create_bucket(Bucket=bucket)

    image_id = str(uuid4())
    timestamp = datetime.now(timezone.utc)
    buffer = io.BytesIO()
    PILImage.new("RGB", (200, 300), "white").save(buffer, format="PNG")
    s3_client.put_object(
        Bucket=image_bucket, Key="original.png", Body=buffer.getvalue()
    )

    client = DynamoClient(table_name)
    client.add_image(
        Image(
            image_id=image_id,
            width=200,
            height=300,
            timestamp_added=timestamp,
            raw_s3_bucket=image_bucket,
            raw_s3_key="original.png",
            receipt_count=1,
        )
    )
    client.add_receipt(
        Receipt(
            image_id=image_id,
            receipt_id=1,
            width=200,
            height=300,
            timestamp_added=timestamp,
            raw_s3_bucket=image_bucket,
            raw_s3_key="original.png",
            top_left={"x": 0.0, "y": 1.0},
            top_right={"x": 1.0, "y": 1.0},
            bottom_left={"x": 0.0, "y": 0.0},
            bottom_right={"x": 1.0, "y": 0.0},
        )
    )
    line_geometry = [
        _geometry(0.1, 0.35, 0.8, 0.7),
        _geometry(0.6, 0.9, 0.4, 0.3),
    ]
    client.add_receipt_lines(
        [
            ReceiptLine(
                image_id=image_id,
                receipt_id=1,
                line_id=index,
                text=text,
                **geometry,
            )
            for index, (text, geometry) in enumerate(
                zip(("LEFT", "RIGHT"), line_geometry), start=1
            )
        ]
    )
    client.add_receipt_words(
        [
            ReceiptWord(
                image_id=image_id,
                receipt_id=1,
                line_id=index,
                word_id=1,
                text=text,
                **geometry,
            )
            for index, (text, geometry) in enumerate(
                zip(("LEFT", "RIGHT"), line_geometry), start=1
            )
        ]
    )
    client.add_receipt_word_labels(
        [
            ReceiptWordLabel(
                image_id=image_id,
                receipt_id=1,
                line_id=index,
                word_id=1,
                label="MERCHANT_NAME" if index == 1 else "GRAND_TOTAL",
                reasoning="test label",
                timestamp_added=timestamp,
                validation_status="VALID",
                label_proposed_by="test",
            )
            for index in (1, 2)
        ]
    )
    return client, s3_client, image_id


@mock_aws
def test_apply_retries_after_transient_commit_failure(monkeypatch):
    """A failed commit transaction must not brick the plan: the status is
    reverted to PLANNED, the reservations are released idempotently, and a
    retry applies cleanly."""
    raw_bucket = "resegment-raw"
    client, s3_client, image_id = _seed_two_line_receipt(
        "ReceiptResegmentRetry", raw_bucket, "resegment-images"
    )

    plan = create_plan(
        {
            "image_id": image_id,
            "source_receipt_id": 1,
            "segments": [
                {"segment_key": "left", "include_line_ids": [1]},
                {"segment_key": "right", "include_line_ids": [2]},
            ],
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )

    real_commit = client.commit_receipt_resegmentation
    calls = {"count": 0}

    def flaky_commit(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated transient commit failure")
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(client, "commit_receipt_resegmentation", flaky_commit)

    apply_event = {
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "create_embeddings": False,
    }
    apply_kwargs = {
        "dynamo_client": client,
        "s3_client": s3_client,
        "raw_bucket": raw_bucket,
        "site_bucket": "resegment-site",
        "chromadb_bucket": "resegment-chroma",
    }

    with pytest.raises(RuntimeError, match="simulated transient commit failure"):
        apply_plan(apply_event, **apply_kwargs)

    stored = resegment_receipt._load_plan(s3_client, raw_bucket, plan["plan_id"])
    assert stored["status"] == "PLANNED"
    # The rollback released the reservations and staged children.
    for output_id in (2, 3):
        assert client.get_receipt_item_type_counts(image_id, output_id) == {}

    result = apply_plan(apply_event, **apply_kwargs)
    assert result["status"] == "APPLIED"
    assert result["output_receipt_ids"] == [2, 3]


@mock_aws
def test_apply_recovers_from_crash_during_committing(monkeypatch):
    """A crash after the COMMITTING marker is persisted but before the commit
    transaction leaves reservation rows behind; the resume path must treat
    them as not-yet-visible outputs and re-run the plan."""
    raw_bucket = "resegment-raw"
    client, s3_client, image_id = _seed_two_line_receipt(
        "ReceiptResegmentCrash", raw_bucket, "resegment-images"
    )

    plan = create_plan(
        {
            "image_id": image_id,
            "source_receipt_id": 1,
            "segments": [
                {"segment_key": "left", "include_line_ids": [1]},
                {"segment_key": "right", "include_line_ids": [2]},
            ],
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )

    # Simulate the crash: reservations exist and the stored plan says
    # COMMITTING, but the commit transaction never ran.
    client.reserve_receipt_ids(image_id, [2, 3], plan["plan_id"])
    stored = resegment_receipt._load_plan(s3_client, raw_bucket, plan["plan_id"])
    stored["status"] = "COMMITTING"
    resegment_receipt._save_plan(s3_client, raw_bucket, stored)

    result = apply_plan(
        {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "create_embeddings": False,
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
        site_bucket="resegment-site",
        chromadb_bucket="resegment-chroma",
    )

    assert result["status"] == "APPLIED"
    assert result["output_receipt_ids"] == [2, 3]
    assert client.get_receipt_item_type_counts(image_id, 1) == {}


@mock_aws
def test_apply_survives_commit_exception_after_transaction_landed(monkeypatch):
    """A lost commit response (DynamoDB committed, then the call raised)
    must not trigger a rollback that would destroy the committed outputs."""
    raw_bucket = "resegment-raw"
    client, s3_client, image_id = _seed_two_line_receipt(
        "ReceiptResegmentLanded", raw_bucket, "resegment-images"
    )

    plan = create_plan(
        {
            "image_id": image_id,
            "source_receipt_id": 1,
            "segments": [
                {"segment_key": "left", "include_line_ids": [1]},
                {"segment_key": "right", "include_line_ids": [2]},
            ],
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
    )

    real_commit = client.commit_receipt_resegmentation

    def commit_then_raise(*args, **kwargs):
        real_commit(*args, **kwargs)
        raise RuntimeError("simulated lost commit response")

    monkeypatch.setattr(client, "commit_receipt_resegmentation", commit_then_raise)

    result = apply_plan(
        {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "create_embeddings": False,
        },
        dynamo_client=client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
        site_bucket="resegment-site",
        chromadb_bucket="resegment-chroma",
    )

    assert result["status"] == "APPLIED"
    assert result["output_receipt_ids"] == [2, 3]
    for receipt_id in (2, 3):
        details = client.get_receipt_details(image_id, receipt_id)
        assert len(details.words) == 1
        assert len(details.labels) == 1


@mock_aws
def test_create_plan_rejects_visible_regions_on_rectangular_strategy():
    """Apply ignores visible_regions for RECTANGULAR plans, so accepting
    them would preview an artifact apply never produces."""
    raw_bucket = "resegment-raw"
    client, s3_client, image_id = _seed_two_line_receipt(
        "ReceiptResegmentRegions", raw_bucket, "resegment-images"
    )

    with pytest.raises(ValueError, match="visible_regions require"):
        create_plan(
            {
                "schema_version": 2,
                "image_id": image_id,
                "source_receipt_id": 1,
                "segments": [
                    {
                        "segment_key": "only",
                        "visible_regions": [
                            {
                                "points": [
                                    {"x": 0.1, "y": 0.1},
                                    {"x": 0.9, "y": 0.1},
                                    {"x": 0.9, "y": 0.9},
                                ]
                            }
                        ],
                    }
                ],
                "assignments": {
                    "lines": [
                        {"line_id": 1, "segment_key": "only"},
                        {"line_id": 2, "segment_key": "only"},
                    ]
                },
                "visualization": {"strategy": "RECTANGULAR"},
            },
            dynamo_client=client,
            s3_client=s3_client,
            raw_bucket=raw_bucket,
        )
