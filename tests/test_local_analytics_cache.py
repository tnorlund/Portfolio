import argparse
import hashlib
import json
import logging
import sqlite3
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

from scripts import local_analytics_cache as cache


def test_component_parser_accepts_image_alias_and_internal_name():
    assert cache._parse_components("dynamodb,images") == {
        "dynamodb",
        "raw_images",
    }
    assert cache._parse_components("raw_images") == {"raw_images"}


def test_dynamodb_wire_json_round_trips_binary_values():
    wire_item = {"PK": {"S": "BINARY#1"}, "payload": {"B": b"\x00\xff"}}

    restored = cache._restore_binary_values(json.loads(cache._json_dump(wire_item)))

    assert restored == wire_item


def test_dynamo_writer_builds_queryable_cache_and_image_index(tmp_path: Path):
    db_path = tmp_path / "dynamodb.sqlite3"
    writer = cache.DynamoSQLiteWriter(db_path)
    writer.add(
        [
            {
                "PK": {"S": "IMAGE#abc"},
                "SK": {"S": "IMAGE"},
                "TYPE": {"S": "IMAGE"},
                "raw_s3_bucket": {"S": "raw-bucket"},
                "raw_s3_key": {"S": "raw/abc.png"},
                "sha256": {"S": "deadbeef"},
                "confidence": {"N": "0.875"},
            }
        ]
    )
    counts = writer.finalize({"table_name": "receipts"})

    with sqlite3.connect(db_path) as connection:
        item = json.loads(
            connection.execute("SELECT item_json FROM images").fetchone()[0]
        )
        image = connection.execute(
            "SELECT bucket, object_key, sha256, status FROM raw_images"
        ).fetchone()

    assert counts == {"IMAGE": 1}
    assert item["confidence"] == 0.875
    assert image == ("raw-bucket", "raw/abc.png", "deadbeef", "pending")


def test_dynamo_writer_uses_item_bucket_before_pulumi_fallback(tmp_path: Path):
    db_path = tmp_path / "dynamodb.sqlite3"
    writer = cache.DynamoSQLiteWriter(db_path, raw_bucket_fallback="pulumi-raw-bucket")
    writer.add(
        [
            {
                "PK": {"S": "IMAGE#missing-bucket"},
                "SK": {"S": "IMAGE"},
                "raw_s3_key": {"S": "raw/missing.png"},
            },
            {
                "PK": {"S": "IMAGE#recorded-bucket"},
                "SK": {"S": "IMAGE"},
                "raw_s3_bucket": {"S": "recorded-raw-bucket"},
                "raw_s3_key": {"S": "raw/recorded.png"},
            },
        ]
    )
    writer.finalize({"table_name": "receipts"})

    with sqlite3.connect(db_path) as connection:
        images = connection.execute(
            "SELECT bucket, object_key FROM raw_images ORDER BY object_key"
        ).fetchall()

    assert images == [
        ("pulumi-raw-bucket", "raw/missing.png"),
        ("recorded-raw-bucket", "raw/recorded.png"),
    ]


def test_dynamo_writer_explicit_bucket_override_wins(tmp_path: Path):
    db_path = tmp_path / "dynamodb.sqlite3"
    writer = cache.DynamoSQLiteWriter(
        db_path,
        raw_bucket_override="override-raw-bucket",
        raw_bucket_fallback="pulumi-raw-bucket",
    )
    writer.add(
        [
            {
                "PK": {"S": "IMAGE#recorded-bucket"},
                "SK": {"S": "IMAGE"},
                "raw_s3_bucket": {"S": "recorded-raw-bucket"},
                "raw_s3_key": {"S": "raw/recorded.png"},
            }
        ]
    )
    writer.finalize({"table_name": "receipts"})

    with sqlite3.connect(db_path) as connection:
        bucket = connection.execute("SELECT bucket FROM raw_images").fetchone()[0]

    assert bucket == "override-raw-bucket"


def test_sync_passes_pulumi_raw_bucket_as_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    monkeypatch.delenv("CHROMADB_BUCKET", raising=False)
    monkeypatch.delenv("RAW_BUCKET", raising=False)
    monkeypatch.setattr(
        cache,
        "_pulumi_outputs",
        lambda _env: {
            "dynamodb_table_name": "receipts-table",
            "raw_bucket_name": "pulumi-raw-bucket",
        },
    )
    session = MagicMock()
    monkeypatch.setattr(cache.boto3, "Session", lambda **_kwargs: session)
    captured = {}

    def fake_sync_dynamodb(
        _client,
        table_name,
        destination,
        _segments,
        _consistent_read,
        raw_bucket_override,
        raw_bucket_fallback,
    ):
        captured.update(
            table_name=table_name,
            override=raw_bucket_override,
            fallback=raw_bucket_fallback,
        )
        destination.write_bytes(b"sqlite")
        return {"valid": True, "path": "dynamodb.sqlite3"}

    monkeypatch.setattr(cache, "sync_dynamodb", fake_sync_dynamodb)
    args = argparse.Namespace(
        components="dynamodb",
        cache_root=tmp_path / "dev",
        env="dev",
        profile=None,
        region=None,
        table_name=None,
        chroma_bucket=None,
        raw_bucket=None,
        scan_segments=8,
        consistent_read=False,
    )

    cache.sync_cache(args)

    assert captured == {
        "table_name": "receipts-table",
        "override": None,
        "fallback": "pulumi-raw-bucket",
    }


def test_safe_object_path_preserves_normal_keys_and_blocks_traversal(tmp_path: Path):
    normal = cache._safe_object_path(tmp_path, "bucket", "raw/2026/a.png")
    traversal = cache._safe_object_path(tmp_path, "bucket", "../secret.png")

    assert normal == tmp_path / "raw-images" / "bucket" / "raw/2026/a.png"
    assert tmp_path / "raw-images" in traversal.parents
    assert ".." not in traversal.parts


def test_invalidate_is_metadata_only_by_default(tmp_path: Path):
    cache_root = tmp_path / "dev"
    cache_root.mkdir()
    db_path = cache_root / "dynamodb.sqlite3"
    db_path.write_bytes(b"keep me")
    cache._write_manifest(
        cache_root,
        {
            "schema_version": cache.SCHEMA_VERSION,
            "valid": True,
            "components": {name: {"valid": True} for name in cache.COMPONENTS},
        },
    )

    manifest = cache.invalidate_cache(cache_root, {"dynamodb"})

    assert db_path.read_bytes() == b"keep me"
    assert manifest["valid"] is False
    assert manifest["components"]["dynamodb"]["valid"] is False
    assert manifest["components"]["chroma"]["valid"] is True


def test_create_local_table_recreates_remote_key_and_index_schema():
    client = MagicMock()
    client.get_waiter.return_value = MagicMock()
    schema = {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "TYPE", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "LocalSecondaryIndexes": [],
    }

    cache._create_local_table(client, "receipts-local", schema)

    kwargs = client.create_table.call_args.kwargs
    assert kwargs["TableName"] == "receipts-local"
    assert kwargs["KeySchema"] == schema["KeySchema"]
    assert kwargs["GlobalSecondaryIndexes"][0]["IndexName"] == "GSITYPE"
    assert "ProvisionedThroughput" in kwargs["GlobalSecondaryIndexes"][0]


def test_local_dynamo_import_progress_log_formats_large_counts(
    tmp_path: Path, monkeypatch, caplog
):
    monkeypatch.setattr(cache, "_delete_local_table", lambda *_args: None)
    monkeypatch.setattr(cache, "_create_local_table", lambda *_args: None)
    monkeypatch.setattr(
        cache,
        "_iter_dynamo_batches",
        lambda _path: iter([[{"PK": {"S": str(index)}}] for index in range(4)]),
    )
    monkeypatch.setattr(
        cache,
        "_write_local_batch",
        lambda _client, _table, items: len(items),
    )

    with caplog.at_level(logging.INFO, logger="local-analytics-cache"):
        imported = cache._import_local_dynamo(
            MagicMock(), "receipts-local", tmp_path / "cache.sqlite3", {}, 1
        )

    assert imported == 4
    assert "DynamoDB Local import: 4 items" in caplog.messages


def test_raw_image_download_is_verified_then_reused(tmp_path: Path):
    payload = b"raw receipt bytes"
    digest = hashlib.sha256(payload).hexdigest()
    s3_client = MagicMock()
    s3_client.get_object.return_value = {
        "Body": BytesIO(payload),
        "ETag": '"etag-1"',
    }

    downloaded = cache._download_raw_image(
        s3_client,
        tmp_path,
        "raw-bucket",
        "raw/receipt.png",
        digest,
        None,
        refresh=False,
    )
    cached = cache._download_raw_image(
        s3_client,
        tmp_path,
        "raw-bucket",
        "raw/receipt.png",
        digest,
        downloaded,
        refresh=False,
    )

    assert downloaded["status"] == "downloaded"
    assert cached["status"] == "cached"
    assert (tmp_path / downloaded["local_path"]).read_bytes() == payload
    s3_client.get_object.assert_called_once()
