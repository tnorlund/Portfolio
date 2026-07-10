import hashlib
import json
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
