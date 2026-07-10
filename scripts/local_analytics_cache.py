#!/usr/bin/env python3
"""Build and manage a fast local cache for receipt analytics.

The cache contains:

* A SQLite mirror of every item in the selected DynamoDB table.
* Native ChromaDB snapshots for the ``lines`` and ``words`` collections.
* Raw S3 images referenced by DynamoDB Image and Receipt records.

DynamoDB is scanned in parallel, ChromaDB uses the repository's optimized
snapshot downloader, and raw images are downloaded concurrently. Existing
images and unchanged ChromaDB versions are reused on subsequent syncs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Sequence

import boto3
from boto3.dynamodb.types import Binary, TypeDeserializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "receipt_chroma"))

SCHEMA_VERSION = 1
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "analytics"
CHROMA_COLLECTIONS = ("lines", "words")
COMPONENTS = ("dynamodb", "chroma", "raw_images")
LOG = logging.getLogger("local-analytics-cache")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (Binary, bytes, bytearray)):
        raw = bytes(value)
        return {"__base64__": base64.b64encode(raw).decode("ascii")}
    if isinstance(value, set):
        return sorted(value, key=str)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    )


def _restore_binary_values(value: Any) -> Any:
    """Restore binary markers after reading cached DynamoDB wire JSON."""
    if isinstance(value, dict):
        if set(value) == {"__base64__"}:
            return base64.b64decode(value["__base64__"])
        return {key: _restore_binary_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_binary_values(item) for item in value]
    return value


def _attribute_value(value: dict[str, Any] | None) -> Any:
    if not value:
        return None
    return TypeDeserializer().deserialize(value)


def _string_attribute(item: dict[str, Any], name: str) -> str | None:
    value = _attribute_value(item.get(name))
    return value if isinstance(value, str) and value else None


def _native_item(item: dict[str, Any]) -> dict[str, Any]:
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in item.items()}


def _safe_object_path(cache_root: Path, bucket: str, key: str) -> Path:
    """Return a readable, traversal-safe local path for an S3 object."""
    parts: list[str] = []
    if key.startswith("/"):
        parts.append("%2F")
    for part in PurePosixPath(key).parts:
        if part in ("/", ""):
            continue
        if part == ".":
            part = "%2E"
        elif part == "..":
            part = "%2E%2E"
        parts.append(part.replace(os.sep, "%2F"))
    if not parts:
        parts = ["_empty-key"]

    path = cache_root / "raw-images" / bucket / Path(*parts)
    raw_root = (cache_root / "raw-images").resolve()
    if raw_root not in path.resolve().parents:
        digest = hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()
        path = cache_root / "raw-images" / "_unsafe" / digest
    return path


def _load_manifest(cache_root: Path) -> dict[str, Any]:
    path = cache_root / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read cache manifest {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _write_manifest(cache_root: Path, manifest: dict[str, Any]) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    path = cache_root / "manifest.json"
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.replace(temp_path, path)


def _pulumi_outputs(env: str) -> dict[str, Any]:
    command = [
        "pulumi",
        "stack",
        "output",
        "--stack",
        f"tnorlund/portfolio/{env}",
        "--json",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT / "infra",
            check=True,
            capture_output=True,
            text=True,
        )
        outputs = json.loads(result.stdout)
        return outputs if isinstance(outputs, dict) else {}
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Pulumi is required for automatic resource discovery. "
            "Install it or pass explicit resource names."
        ) from exc
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(
            f"Could not load Pulumi outputs for {env}: {detail.strip()}"
        ) from exc


@dataclass(frozen=True)
class SourceConfig:
    table_name: str | None
    chroma_bucket: str | None
    raw_bucket: str | None


def _resolve_sources(args: argparse.Namespace, components: set[str]) -> SourceConfig:
    table_name = args.table_name or os.environ.get("DYNAMODB_TABLE_NAME")
    chroma_bucket = args.chroma_bucket or os.environ.get("CHROMADB_BUCKET")
    raw_bucket = args.raw_bucket or os.environ.get("RAW_BUCKET")

    needs_discovery = ("dynamodb" in components and not table_name) or (
        "chroma" in components and not chroma_bucket
    )
    outputs: dict[str, Any] = _pulumi_outputs(args.env) if needs_discovery else {}
    table_name = table_name or outputs.get("dynamodb_table_name")
    chroma_bucket = chroma_bucket or outputs.get("embedding_chromadb_bucket_name")
    raw_bucket = raw_bucket or outputs.get("raw_bucket_name")

    if "dynamodb" in components and not table_name:
        raise RuntimeError(
            "DynamoDB table was not found. Pass --table-name or set "
            "DYNAMODB_TABLE_NAME."
        )
    if "chroma" in components and not chroma_bucket:
        raise RuntimeError(
            "ChromaDB bucket was not found. Pass --chroma-bucket or set "
            "CHROMADB_BUCKET."
        )
    return SourceConfig(table_name, chroma_bucket, raw_bucket)


def _parse_components(value: str) -> set[str]:
    requested = {part.strip() for part in value.split(",")}
    if "images" in requested:
        requested.remove("images")
        requested.add("raw_images")
    if "all" in requested:
        return set(COMPONENTS)
    unknown = requested - set(COMPONENTS)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"Unknown component(s): {', '.join(sorted(unknown))}"
        )
    return requested


class DynamoSQLiteWriter:
    """Thread-safe batched writer used by parallel DynamoDB scan workers."""

    def __init__(
        self,
        path: Path,
        raw_bucket_override: str | None = None,
        raw_bucket_fallback: str | None = None,
    ):
        self.path = path
        self.raw_bucket_override = raw_bucket_override
        self.raw_bucket_fallback = raw_bucket_fallback
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=MEMORY")
        self.connection.executescript("""
            CREATE TABLE dynamo_items (
                pk TEXT NOT NULL,
                sk TEXT NOT NULL,
                entity_type TEXT,
                image_id TEXT,
                receipt_id INTEGER,
                raw_s3_bucket TEXT,
                raw_s3_key TEXT,
                item_json TEXT NOT NULL,
                dynamodb_json TEXT NOT NULL,
                PRIMARY KEY (pk, sk)
            ) WITHOUT ROWID;

            CREATE TABLE raw_images (
                bucket TEXT NOT NULL,
                object_key TEXT NOT NULL,
                sha256 TEXT,
                local_path TEXT,
                etag TEXT,
                size_bytes INTEGER,
                last_modified TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                PRIMARY KEY (bucket, object_key)
            ) WITHOUT ROWID;

            CREATE TABLE cache_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;
            """)
        self.lock = threading.Lock()
        self.row_count = 0

    @staticmethod
    def _row(item: dict[str, Any]) -> tuple[Any, ...]:
        native = _native_item(item)
        pk = str(native.get("PK", ""))
        sk = str(native.get("SK", ""))
        entity_type = native.get("TYPE")
        if not isinstance(entity_type, str):
            entity_type = sk.split("#", 1)[0] if sk else None
        image_id = pk.split("#", 1)[1] if pk.startswith("IMAGE#") else None
        receipt_id: int | None = None
        if sk.startswith("RECEIPT#"):
            try:
                receipt_id = int(sk.split("#", 2)[1])
            except (IndexError, ValueError):
                pass
        return (
            pk,
            sk,
            entity_type,
            image_id,
            receipt_id,
            native.get("raw_s3_bucket"),
            native.get("raw_s3_key"),
            _json_dump(native),
            _json_dump(item),
        )

    def add(self, items: Sequence[dict[str, Any]]) -> None:
        rows = [self._row(item) for item in items]
        image_rows: list[tuple[str, str, str | None]] = []
        for item in items:
            bucket = (
                self.raw_bucket_override
                or _string_attribute(item, "raw_s3_bucket")
                or self.raw_bucket_fallback
            )
            key = _string_attribute(item, "raw_s3_key")
            sha256 = _string_attribute(item, "sha256")
            if bucket and key:
                image_rows.append((bucket, key, sha256))

        with self.lock:
            self.connection.executemany(
                """
                INSERT OR REPLACE INTO dynamo_items (
                    pk, sk, entity_type, image_id, receipt_id,
                    raw_s3_bucket, raw_s3_key, item_json, dynamodb_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self.connection.executemany(
                """
                INSERT INTO raw_images (bucket, object_key, sha256)
                VALUES (?, ?, ?)
                ON CONFLICT(bucket, object_key) DO UPDATE SET
                    sha256 = COALESCE(raw_images.sha256, excluded.sha256)
                """,
                image_rows,
            )
            self.row_count += len(rows)

    def finalize(self, metadata: dict[str, Any]) -> dict[str, int]:
        with self.lock:
            self.connection.execute(
                "CREATE INDEX dynamo_items_type_idx ON dynamo_items(entity_type)"
            )
            self.connection.execute(
                "CREATE INDEX dynamo_items_image_idx ON dynamo_items(image_id)"
            )
            self.connection.execute("""CREATE INDEX dynamo_items_raw_s3_idx
                ON dynamo_items(raw_s3_bucket, raw_s3_key)""")
            self.connection.executescript("""
                CREATE VIEW images AS
                SELECT * FROM dynamo_items WHERE entity_type = 'IMAGE';

                CREATE VIEW receipts AS
                SELECT * FROM dynamo_items WHERE entity_type = 'RECEIPT';
                """)
            self.connection.executemany(
                "INSERT OR REPLACE INTO cache_metadata(key, value) VALUES (?, ?)",
                [(key, _json_dump(value)) for key, value in metadata.items()],
            )
            entity_counts = dict(
                self.connection.execute(
                    """SELECT COALESCE(entity_type, '(unknown)'), COUNT(*)
                    FROM dynamo_items GROUP BY entity_type"""
                ).fetchall()
            )
            self.row_count = int(
                self.connection.execute("SELECT COUNT(*) FROM dynamo_items").fetchone()[
                    0
                ]
            )
            self.connection.execute("ANALYZE")
            self.connection.commit()
            self.connection.close()
        return {str(key): int(value) for key, value in entity_counts.items()}

    def abort(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass


def _scan_segment(
    client: Any,
    table_name: str,
    segment: int,
    total_segments: int,
    consistent_read: bool,
    writer: DynamoSQLiteWriter,
) -> dict[str, float | int]:
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "Segment": segment,
        "TotalSegments": total_segments,
        "ConsistentRead": consistent_read,
        "ReturnConsumedCapacity": "TOTAL",
    }
    pages = 0
    scanned = 0
    capacity = 0.0
    while True:
        response = client.scan(**kwargs)
        items = response.get("Items", [])
        writer.add(items)
        pages += 1
        scanned += int(response.get("ScannedCount", len(items)))
        capacity += float(response.get("ConsumedCapacity", {}).get("CapacityUnits", 0))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return {"pages": pages, "scanned": scanned, "capacity_units": capacity}
        kwargs["ExclusiveStartKey"] = last_key


def sync_dynamodb(
    client: Any,
    table_name: str,
    destination: Path,
    segments: int,
    consistent_read: bool,
    raw_bucket_override: str | None,
    raw_bucket_fallback: str | None,
) -> dict[str, Any]:
    LOG.info("Scanning DynamoDB table %s with %d segments", table_name, segments)
    table = client.describe_table(TableName=table_name)["Table"]
    writer = DynamoSQLiteWriter(
        destination,
        raw_bucket_override=raw_bucket_override,
        raw_bucket_fallback=raw_bucket_fallback,
    )
    segment_stats: list[dict[str, float | int]] = []
    try:
        with ThreadPoolExecutor(max_workers=segments) as executor:
            futures = [
                executor.submit(
                    _scan_segment,
                    client,
                    table_name,
                    segment,
                    segments,
                    consistent_read,
                    writer,
                )
                for segment in range(segments)
            ]
            for future in as_completed(futures):
                stats = future.result()
                segment_stats.append(stats)
                LOG.info(
                    "DynamoDB scan: %d/%d segments complete",
                    len(segment_stats),
                    segments,
                )

        scan_stats = {
            "pages": sum(int(item["pages"]) for item in segment_stats),
            "scanned": sum(int(item["scanned"]) for item in segment_stats),
            "capacity_units": round(
                sum(float(item["capacity_units"]) for item in segment_stats), 3
            ),
        }
        synced_at = _utc_now()
        entity_counts = writer.finalize(
            {
                "table_name": table_name,
                "table_arn": table.get("TableArn"),
                "synced_at": synced_at,
                "scan": scan_stats,
            }
        )
    except Exception:
        writer.abort()
        raise

    global_indexes = [
        {
            "IndexName": index["IndexName"],
            "KeySchema": index["KeySchema"],
            "Projection": index["Projection"],
        }
        for index in table.get("GlobalSecondaryIndexes", [])
    ]
    local_indexes = [
        {
            "IndexName": index["IndexName"],
            "KeySchema": index["KeySchema"],
            "Projection": index["Projection"],
        }
        for index in table.get("LocalSecondaryIndexes", [])
    ]
    return {
        "valid": True,
        "path": "dynamodb.sqlite3",
        "table_name": table_name,
        "table_arn": table.get("TableArn"),
        "described_item_count": int(table.get("ItemCount", 0)),
        "table_size_bytes": int(table.get("TableSizeBytes", 0)),
        "row_count": writer.row_count,
        "entity_counts": entity_counts,
        "scan": scan_stats,
        "consistent_read": consistent_read,
        "table_schema": {
            "KeySchema": table["KeySchema"],
            "AttributeDefinitions": table["AttributeDefinitions"],
            "GlobalSecondaryIndexes": global_indexes,
            "LocalSecondaryIndexes": local_indexes,
        },
        "synced_at": synced_at,
    }


def _chroma_version(s3_client: Any, bucket: str, collection: str) -> str:
    response = s3_client.get_object(
        Bucket=bucket,
        Key=f"{collection}/snapshot/latest-pointer.txt",
    )
    return response["Body"].read().decode("utf-8").strip()


def _download_chroma_collection(
    s3_client: Any,
    bucket: str,
    collection: str,
    destination: Path,
    workers: int,
) -> dict[str, Any]:
    from receipt_chroma.s3 import download_snapshot_atomic

    destination.mkdir(parents=True, exist_ok=True)
    result = download_snapshot_atomic(
        bucket=bucket,
        collection=collection,
        local_path=str(destination),
        verify_integrity=True,
        s3_client=s3_client,
        parallel=True,
        max_workers=workers,
    )
    if result.get("status") != "downloaded":
        raise RuntimeError(f"ChromaDB {collection} download failed: {result}")
    return result


def sync_chroma(
    s3_client: Any,
    bucket: str,
    cache_root: Path,
    staging_root: Path,
    previous: dict[str, Any],
    workers: int,
    force: bool,
) -> tuple[dict[str, Any], dict[str, Path]]:
    LOG.info("Checking ChromaDB snapshot versions in s3://%s", bucket)
    versions: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_chroma_version, s3_client, bucket, name): name
            for name in CHROMA_COLLECTIONS
        }
        for future in as_completed(futures):
            versions[futures[future]] = future.result()

    previous_collections = previous.get("collections", {})
    collection_stats: dict[str, Any] = {}
    staged: dict[str, Path] = {}
    downloads: dict[Any, tuple[str, Path]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        for collection in CHROMA_COLLECTIONS:
            current_path = cache_root / "chroma" / collection
            old = previous_collections.get(collection, {})
            unchanged = (
                not force
                and previous.get("valid") is True
                and old.get("version_id") == versions[collection]
                and any(current_path.rglob("chroma.sqlite3"))
            )
            if unchanged:
                collection_stats[collection] = {
                    **old,
                    "version_id": versions[collection],
                    "reused": True,
                }
                LOG.info(
                    "Reusing ChromaDB %s snapshot %s", collection, versions[collection]
                )
                continue

            destination = staging_root / "chroma" / collection
            future = executor.submit(
                _download_chroma_collection,
                s3_client,
                bucket,
                collection,
                destination,
                workers,
            )
            downloads[future] = (collection, destination)

        for future in as_completed(downloads):
            collection, destination = downloads[future]
            result = future.result()
            collection_stats[collection] = {
                "version_id": result.get("version_id"),
                "path": f"chroma/{collection}",
                "reused": False,
            }
            staged[collection] = destination
            LOG.info("Downloaded ChromaDB %s snapshot", collection)

    return (
        {
            "valid": True,
            "bucket": bucket,
            "collections": collection_stats,
            "synced_at": _utc_now(),
        },
        staged,
    )


def _iter_raw_image_rows(db_path: Path) -> Iterator[tuple[str, str, str | None]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT bucket, object_key, sha256 FROM raw_images ORDER BY bucket, object_key"
        )
        yield from rows


def _load_previous_images(db_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                """SELECT bucket, object_key, local_path, etag, size_bytes,
                last_modified, status FROM raw_images"""
            )
            return {
                (row[0], row[1]): {
                    "local_path": row[2],
                    "etag": row[3],
                    "size_bytes": row[4],
                    "last_modified": row[5],
                    "status": row[6],
                }
                for row in rows
            }
    except sqlite3.Error:
        return {}


def _download_raw_image(
    s3_client: Any,
    cache_root: Path,
    bucket: str,
    key: str,
    expected_sha256: str | None,
    previous: dict[str, Any] | None,
    refresh: bool,
) -> dict[str, Any]:
    path = _safe_object_path(cache_root, bucket, key)
    relative_path = str(path.relative_to(cache_root))
    previous = previous or {}

    if path.exists() and not refresh:
        previous_size = previous.get("size_bytes")
        if previous_size is None or path.stat().st_size == previous_size:
            return {
                "bucket": bucket,
                "key": key,
                "local_path": relative_path,
                "etag": previous.get("etag"),
                "size_bytes": path.stat().st_size,
                "last_modified": previous.get("last_modified"),
                "status": "cached",
            }

    if path.exists() and refresh:
        head = s3_client.head_object(Bucket=bucket, Key=key)
        remote_etag = str(head.get("ETag", "")).strip('"') or None
        remote_size = int(head.get("ContentLength", 0))
        if (
            previous.get("etag")
            and previous["etag"] == remote_etag
            and path.stat().st_size == remote_size
        ):
            return {
                "bucket": bucket,
                "key": key,
                "local_path": relative_path,
                "etag": remote_etag,
                "size_bytes": remote_size,
                "last_modified": head.get("LastModified"),
                "status": "cached",
            }

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    hasher = hashlib.sha256()
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        size = 0
        try:
            with temp_path.open("wb") as output:
                while True:
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
        finally:
            body.close()

        digest = hasher.hexdigest()
        if expected_sha256 and digest.lower() != expected_sha256.lower():
            raise RuntimeError(
                f"SHA256 mismatch for s3://{bucket}/{key}: "
                f"expected {expected_sha256}, got {digest}"
            )
        os.replace(temp_path, path)
        return {
            "bucket": bucket,
            "key": key,
            "local_path": relative_path,
            "etag": str(response.get("ETag", "")).strip('"') or None,
            "size_bytes": size,
            "last_modified": response.get("LastModified"),
            "status": "downloaded",
        }
    finally:
        temp_path.unlink(missing_ok=True)


def _save_image_results(db_path: Path, results: Iterable[dict[str, Any]]) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            UPDATE raw_images SET
                local_path = ?, etag = ?, size_bytes = ?, last_modified = ?,
                status = ?, error = ?
            WHERE bucket = ? AND object_key = ?
            """,
            [
                (
                    item.get("local_path"),
                    item.get("etag"),
                    item.get("size_bytes"),
                    str(item.get("last_modified") or "") or None,
                    item["status"],
                    item.get("error"),
                    item["bucket"],
                    item["key"],
                )
                for item in results
            ],
        )
        connection.commit()


def sync_raw_images(
    s3_client: Any,
    cache_root: Path,
    db_path: Path,
    previous_db_path: Path,
    workers: int,
    refresh: bool,
) -> dict[str, Any]:
    rows = list(_iter_raw_image_rows(db_path))
    previous = _load_previous_images(previous_db_path)
    LOG.info("Syncing %d raw S3 images with %d workers", len(rows), workers)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _download_raw_image,
                s3_client,
                cache_root,
                bucket,
                key,
                sha256,
                previous.get((bucket, key)),
                refresh,
            ): (bucket, key)
            for bucket, key, sha256 in rows
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            bucket, key = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # keep all failures for a useful summary
                failure = {
                    "bucket": bucket,
                    "key": key,
                    "status": "failed",
                    "error": str(exc),
                }
                results.append(failure)
                failures.append(failure)
            if completed % 100 == 0 or completed == len(futures):
                LOG.info("Raw images: %d/%d complete", completed, len(futures))

    _save_image_results(db_path, results)
    if failures:
        examples = "; ".join(
            f"s3://{item['bucket']}/{item['key']}: {item['error']}"
            for item in failures[:3]
        )
        raise RuntimeError(f"{len(failures)} raw image downloads failed: {examples}")

    downloaded = sum(item["status"] == "downloaded" for item in results)
    cached = sum(item["status"] == "cached" for item in results)
    return {
        "valid": True,
        "path": "raw-images",
        "object_count": len(results),
        "size_bytes": sum(int(item.get("size_bytes") or 0) for item in results),
        "downloaded": downloaded,
        "reused": cached,
        "synced_at": _utc_now(),
    }


def _swap_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    old = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.old")
    if destination.exists():
        os.replace(destination, old)
    try:
        os.replace(source, destination)
    except Exception:
        if old.exists() and not destination.exists():
            os.replace(old, destination)
        raise
    shutil.rmtree(old, ignore_errors=True)


def sync_cache(args: argparse.Namespace) -> dict[str, Any]:
    components = _parse_components(args.components)
    if (
        "raw_images" in components
        and not (args.cache_root / "dynamodb.sqlite3").exists()
    ):
        components.add("dynamodb")
    sources = _resolve_sources(args, components)
    cache_root: Path = args.cache_root
    cache_root.mkdir(parents=True, exist_ok=True)
    staging_root = cache_root / f".staging-{uuid.uuid4().hex}"
    staging_root.mkdir()
    marker = cache_root / ".sync-in-progress"
    marker.write_text(_utc_now() + "\n", encoding="utf-8")
    manifest = _load_manifest(cache_root)
    manifest.setdefault("components", {})
    manifest.update(
        {
            "schema_version": SCHEMA_VERSION,
            "environment": args.env,
            "cache_root": str(cache_root),
        }
    )
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3_client = session.client("s3")
    dynamo_temp = staging_root / "dynamodb.sqlite3"
    active_db = cache_root / "dynamodb.sqlite3"
    db_for_images = dynamo_temp if "dynamodb" in components else active_db
    staged_chroma: dict[str, Path] = {}
    raw_bucket_override = args.raw_bucket or os.environ.get("RAW_BUCKET")

    try:
        if "dynamodb" in components:
            manifest["components"]["dynamodb"] = sync_dynamodb(
                session.client("dynamodb"),
                str(sources.table_name),
                dynamo_temp,
                args.scan_segments,
                args.consistent_read,
                raw_bucket_override,
                sources.raw_bucket,
            )

        if "raw_images" in components:
            if not db_for_images.exists():
                raise RuntimeError("DynamoDB cache is required to discover raw images")
            manifest["components"]["raw_images"] = sync_raw_images(
                s3_client,
                cache_root,
                db_for_images,
                active_db,
                args.image_workers,
                args.refresh_images,
            )

        if "chroma" in components:
            chroma_stats, staged_chroma = sync_chroma(
                s3_client,
                str(sources.chroma_bucket),
                cache_root,
                staging_root,
                manifest["components"].get("chroma", {}),
                args.chroma_workers,
                args.force_chroma,
            )
            manifest["components"]["chroma"] = chroma_stats

        if "dynamodb" in components:
            os.replace(dynamo_temp, active_db)
        for collection, staged_path in staged_chroma.items():
            _swap_directory(staged_path, cache_root / "chroma" / collection)

        manifest["updated_at"] = _utc_now()
        manifest.setdefault("created_at", manifest["updated_at"])
        manifest["valid"] = all(
            manifest["components"].get(name, {}).get("valid") is True
            for name in COMPONENTS
        )
        if manifest["valid"]:
            manifest.pop("invalidated_at", None)
        _write_manifest(cache_root, manifest)
        return manifest
    finally:
        marker.unlink(missing_ok=True)
        shutil.rmtree(staging_root, ignore_errors=True)


def _component_paths(cache_root: Path, component: str) -> list[Path]:
    return {
        "dynamodb": [cache_root / "dynamodb.sqlite3"],
        "chroma": [cache_root / "chroma"],
        "raw_images": [cache_root / "raw-images"],
    }[component]


def invalidate_cache(
    cache_root: Path, components: set[str], purge: bool = False
) -> dict[str, Any]:
    manifest = _load_manifest(cache_root)
    invalidated_at = _utc_now()
    selected = set(COMPONENTS) if "all" in components else components

    if purge and selected == set(COMPONENTS):
        shutil.rmtree(cache_root, ignore_errors=True)
        return {"valid": False, "purged": True, "components": sorted(selected)}

    manifest.setdefault("components", {})
    for component in selected:
        state = manifest["components"].setdefault(component, {})
        state["valid"] = False
        state["invalidated_at"] = invalidated_at
        if purge:
            for path in _component_paths(cache_root, component):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["valid"] = False
    manifest["invalidated_at"] = invalidated_at
    _write_manifest(cache_root, manifest)
    return manifest


def _local_dynamo_client(endpoint_url: str, region: str | None = None) -> Any:
    return boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region or "us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
        config=Config(
            connect_timeout=0.25,
            read_timeout=1,
            retries={"max_attempts": 0},
        ),
    )


def _container_name(env: str, port: int) -> str:
    safe_env = re.sub(r"[^a-zA-Z0-9_.-]+", "-", env).strip("-") or "cache"
    return f"portfolio-dynamodb-local-{safe_env}-{port}"


def _start_dynamodb_local_container(args: argparse.Namespace) -> str:
    name = _container_name(args.env, args.port)
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0:
        if inspect.stdout.strip() != "true":
            subprocess.run(["docker", "start", name], check=True)
        return name

    data_dir = args.cache_root / "dynamodb-local"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--publish",
                f"{args.port}:8000",
                "--volume",
                f"{data_dir}:/home/dynamodblocal/data",
                args.docker_image,
                "-jar",
                "DynamoDBLocal.jar",
                "-sharedDb",
                "-dbPath",
                "./data",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Docker is required to start DynamoDB Local; use --endpoint-url "
            "with an existing compatible server instead"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Could not start DynamoDB Local: {detail}") from exc
    return name


def _wait_for_dynamodb(endpoint_url: str, region: str | None) -> Any:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        client = _local_dynamo_client(endpoint_url, region)
        try:
            client.list_tables(Limit=1)
            return client
        except (BotoCoreError, ClientError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(
        f"DynamoDB Local did not become ready at {endpoint_url}: {last_error}"
    )


def _table_exists(client: Any, table_name: str) -> bool:
    try:
        client.describe_table(TableName=table_name)
        return True
    except client.exceptions.ResourceNotFoundException:
        return False


def _delete_local_table(client: Any, table_name: str) -> None:
    if not _table_exists(client, table_name):
        return
    client.delete_table(TableName=table_name)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not _table_exists(client, table_name):
            return
        time.sleep(0.1)
    raise RuntimeError(f"Timed out deleting local DynamoDB table {table_name}")


def _create_local_table(client: Any, table_name: str, schema: dict[str, Any]) -> None:
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "KeySchema": schema["KeySchema"],
        "AttributeDefinitions": schema["AttributeDefinitions"],
        "ProvisionedThroughput": {
            "ReadCapacityUnits": 100,
            "WriteCapacityUnits": 100,
        },
    }
    global_indexes = []
    for index in schema.get("GlobalSecondaryIndexes", []):
        global_indexes.append(
            {
                **index,
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 100,
                    "WriteCapacityUnits": 100,
                },
            }
        )
    if global_indexes:
        kwargs["GlobalSecondaryIndexes"] = global_indexes
    if schema.get("LocalSecondaryIndexes"):
        kwargs["LocalSecondaryIndexes"] = schema["LocalSecondaryIndexes"]
    client.create_table(**kwargs)
    client.get_waiter("table_exists").wait(
        TableName=table_name,
        WaiterConfig={"Delay": 1, "MaxAttempts": 30},
    )


def _write_local_batch(
    client: Any, table_name: str, items: Sequence[dict[str, Any]]
) -> int:
    request_items = {table_name: [{"PutRequest": {"Item": item}} for item in items]}
    for attempt in range(10):
        response = client.batch_write_item(RequestItems=request_items)
        request_items = response.get("UnprocessedItems", {})
        if not request_items.get(table_name):
            return len(items)
        time.sleep(min(0.01 * (2**attempt), 1.0))
    remaining = len(request_items.get(table_name, []))
    raise RuntimeError(f"DynamoDB Local left {remaining} items unprocessed")


def _iter_dynamo_batches(
    db_path: Path, size: int = 25
) -> Iterator[list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        cursor = connection.execute("SELECT dynamodb_json FROM dynamo_items")
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                return
            yield [_restore_binary_values(json.loads(row[0])) for row in rows]


def _import_local_dynamo(
    client: Any,
    table_name: str,
    db_path: Path,
    schema: dict[str, Any],
    workers: int,
) -> int:
    _delete_local_table(client, table_name)
    _create_local_table(client, table_name, schema)
    imported = 0
    pending: list[Any] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in _iter_dynamo_batches(db_path):
            pending.append(
                executor.submit(_write_local_batch, client, table_name, batch)
            )
            if len(pending) >= workers * 4:
                imported += sum(future.result() for future in pending)
                pending.clear()
                LOG.info("DynamoDB Local import: %s items", f"{imported:,}")
        imported += sum(future.result() for future in pending)
    return imported


def serve_dynamodb_cache(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_manifest(args.cache_root)
    state = manifest.get("components", {}).get("dynamodb", {})
    db_path = args.cache_root / state.get("path", "dynamodb.sqlite3")
    if state.get("valid") is not True or not db_path.exists():
        raise RuntimeError("Sync a valid DynamoDB cache before serving it")

    endpoint_url = args.endpoint_url or f"http://127.0.0.1:{args.port}"
    try:
        client = _local_dynamo_client(endpoint_url, args.region)
        client.list_tables(Limit=1)
        container = None
    except (BotoCoreError, ClientError):
        if args.no_docker:
            raise RuntimeError(f"No DynamoDB-compatible server at {endpoint_url}")
        container = _start_dynamodb_local_container(args)
        client = _wait_for_dynamodb(endpoint_url, args.region)

    table_name = state["table_name"]
    local_state_path = args.cache_root / "dynamodb-local-state.json"
    local_state: dict[str, Any] = {}
    if local_state_path.exists():
        try:
            local_state = json.loads(local_state_path.read_text())
        except (OSError, json.JSONDecodeError):
            local_state = {}
    current = (
        not args.force_import
        and local_state.get("source_synced_at") == state.get("synced_at")
        and local_state.get("endpoint_url") == endpoint_url
        and _table_exists(client, table_name)
    )
    if current:
        imported = int(local_state.get("item_count", state.get("row_count", 0)))
        LOG.info("DynamoDB Local already matches the cache; skipping import")
    else:
        LOG.info("Importing the cache into DynamoDB Local at %s", endpoint_url)
        imported = _import_local_dynamo(
            client,
            table_name,
            db_path,
            state["table_schema"],
            args.import_workers,
        )
        local_state = {
            "endpoint_url": endpoint_url,
            "table_name": table_name,
            "source_synced_at": state.get("synced_at"),
            "item_count": imported,
            "container": container,
            "hydrated_at": _utc_now(),
        }
        temp = local_state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(local_state, indent=2, sort_keys=True) + "\n")
        os.replace(temp, local_state_path)

    return {
        "endpoint_url": endpoint_url,
        "table_name": table_name,
        "item_count": imported,
        "container": container or local_state.get("container"),
        "dynamodb_env": {
            "DYNAMODB_ENDPOINT_URL": endpoint_url,
            "DYNAMODB_TABLE_NAME": table_name,
        },
        "chroma": {
            name: str(args.cache_root / "chroma" / name) for name in CHROMA_COLLECTIONS
        },
    }


def stop_dynamodb_cache(args: argparse.Namespace) -> dict[str, Any]:
    name = _container_name(args.env, args.port)
    result = subprocess.run(["docker", "stop", name], capture_output=True, text=True)
    if result.returncode != 0 and "No such container" not in result.stderr:
        raise RuntimeError(result.stderr.strip())
    return {"container": name, "stopped": result.returncode == 0}


def _validate_dynamodb_local(cache_root: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    db_path = cache_root / state.get("path", "dynamodb.sqlite3")
    if not db_path.exists():
        return [f"DynamoDB cache is missing: {db_path}"]
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            count = connection.execute("SELECT COUNT(*) FROM dynamo_items").fetchone()[
                0
            ]
    except sqlite3.Error as exc:
        return [f"DynamoDB cache cannot be read: {exc}"]
    if quick_check != "ok":
        errors.append(f"DynamoDB SQLite quick_check failed: {quick_check}")
    if int(count) != int(state.get("row_count", -1)):
        errors.append(
            f"DynamoDB row count is {count}; manifest expects {state.get('row_count')}"
        )
    return errors


def _validate_chroma_local(cache_root: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for collection in CHROMA_COLLECTIONS:
        path = cache_root / "chroma" / collection
        sqlite_files = list(path.rglob("chroma.sqlite3")) if path.exists() else []
        if not sqlite_files:
            errors.append(f"ChromaDB {collection} cache is missing from {path}")
            continue
        try:
            with sqlite3.connect(
                f"file:{sqlite_files[0]}?mode=ro", uri=True
            ) as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                errors.append(f"ChromaDB {collection} quick_check failed: {check}")
        except sqlite3.Error as exc:
            errors.append(f"ChromaDB {collection} cannot be read: {exc}")
    return errors


def _validate_images_local(cache_root: Path, db_path: Path) -> list[str]:
    errors: list[str] = []
    if not db_path.exists():
        return ["Raw images cannot be checked without the DynamoDB cache"]
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT bucket, object_key, local_path, size_bytes, status FROM raw_images"
            ).fetchall()
    except sqlite3.Error as exc:
        return [f"Raw image index cannot be read: {exc}"]
    for bucket, key, local_path, size, status in rows:
        if status not in ("downloaded", "cached") or not local_path:
            errors.append(f"Raw image is not cached: s3://{bucket}/{key}")
            continue
        path = cache_root / local_path
        if not path.exists():
            errors.append(f"Raw image file is missing: {path}")
        elif size is not None and path.stat().st_size != size:
            errors.append(f"Raw image size mismatch: {path}")
    return errors


def _validate_image_remote(
    s3_client: Any, row: tuple[str, str, str | None, int | None]
) -> str | None:
    bucket, key, etag, size = row
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        return f"Cannot read s3://{bucket}/{key}: {exc}"
    remote_etag = str(head.get("ETag", "")).strip('"') or None
    if etag and etag != remote_etag:
        return f"Raw image ETag changed: s3://{bucket}/{key}"
    if size is not None and int(head.get("ContentLength", -1)) != int(size):
        return f"Raw image size changed: s3://{bucket}/{key}"
    return None


def validate_cache(args: argparse.Namespace) -> dict[str, Any]:
    cache_root: Path = args.cache_root
    manifest = _load_manifest(cache_root)
    errors: list[str] = []
    warnings: list[str] = []
    if not manifest:
        return {"valid": False, "errors": [f"No cache exists at {cache_root}"]}
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("Cache manifest schema is unsupported")
    if (cache_root / ".sync-in-progress").exists():
        errors.append("A cache sync is still in progress or was interrupted")

    selected = _parse_components(args.components)
    component_states = manifest.get("components", {})
    for component in selected:
        state = component_states.get(component, {})
        if state.get("valid") is not True:
            errors.append(f"{component} cache is invalidated or incomplete")
            continue
        if component == "dynamodb":
            errors.extend(_validate_dynamodb_local(cache_root, state))
        elif component == "chroma":
            errors.extend(_validate_chroma_local(cache_root, state))
        elif component == "raw_images":
            errors.extend(
                _validate_images_local(cache_root, cache_root / "dynamodb.sqlite3")
            )

    if not args.local_only:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        if "dynamodb" in selected and component_states.get("dynamodb", {}).get("valid"):
            state = component_states["dynamodb"]
            table = session.client("dynamodb").describe_table(
                TableName=state["table_name"]
            )["Table"]
            if state.get("table_arn") and state["table_arn"] != table.get("TableArn"):
                errors.append("DynamoDB cache points at a different table ARN")
            if int(state.get("described_item_count", -1)) != int(
                table.get("ItemCount", -2)
            ):
                errors.append("DynamoDB approximate item count changed; sync the cache")
            warnings.append(
                "DynamoDB DescribeTable item counts are approximate; only a sync "
                "can detect same-count item updates."
            )

        s3_client = session.client("s3")
        if "chroma" in selected and component_states.get("chroma", {}).get("valid"):
            state = component_states["chroma"]
            for collection in CHROMA_COLLECTIONS:
                remote = _chroma_version(s3_client, state["bucket"], collection)
                local = (
                    state.get("collections", {}).get(collection, {}).get("version_id")
                )
                if remote != local:
                    errors.append(
                        f"ChromaDB {collection} changed ({local} -> {remote}); sync the cache"
                    )

        if (
            args.deep
            and "raw_images" in selected
            and (cache_root / "dynamodb.sqlite3").exists()
        ):
            db_path = cache_root / "dynamodb.sqlite3"
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
                rows = connection.execute(
                    "SELECT bucket, object_key, etag, size_bytes FROM raw_images"
                ).fetchall()
            with ThreadPoolExecutor(max_workers=args.image_workers) as executor:
                remote_errors = executor.map(
                    lambda row: _validate_image_remote(s3_client, row), rows
                )
                errors.extend(error for error in remote_errors if error)

    return {
        "valid": not errors,
        "cache_root": str(cache_root),
        "components": sorted(selected),
        "errors": errors,
        "warnings": warnings,
        "validated_at": _utc_now(),
    }


def _print_status(manifest: dict[str, Any], cache_root: Path) -> None:
    if not manifest:
        print(f"No analytics cache at {cache_root}")
        return
    print(f"Cache: {cache_root}")
    print(f"Environment: {manifest.get('environment', 'unknown')}")
    print(f"Valid: {manifest.get('valid', False)}")
    print(f"Updated: {manifest.get('updated_at', 'never')}")
    components = manifest.get("components", {})
    dynamo = components.get("dynamodb", {})
    print(
        "DynamoDB: "
        f"valid={dynamo.get('valid', False)} rows={dynamo.get('row_count', 0):,} "
        f"path={dynamo.get('path', '-')}"
    )
    chroma = components.get("chroma", {})
    versions = ", ".join(
        f"{name}={chroma.get('collections', {}).get(name, {}).get('version_id', '-')}"
        for name in CHROMA_COLLECTIONS
    )
    print(f"ChromaDB: valid={chroma.get('valid', False)} {versions}")
    images = components.get("raw_images", {})
    print(
        "Raw images: "
        f"valid={images.get('valid', False)} files={images.get('object_count', 0):,} "
        f"size={int(images.get('size_bytes', 0)) / 1024 / 1024:.1f} MiB"
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env", default="dev", help="Pulumi environment (default: dev)"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Cache parent directory (the environment is appended)",
    )
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--region", help="AWS region override")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Refresh local analytics data")
    _add_common_arguments(sync)
    sync.add_argument(
        "--components",
        default="all",
        help="Comma-separated: dynamodb,chroma,images (default: all)",
    )
    sync.add_argument("--table-name", help="DynamoDB table override")
    sync.add_argument("--chroma-bucket", help="ChromaDB S3 bucket override")
    sync.add_argument(
        "--raw-bucket",
        help="Override the raw S3 bucket recorded in DynamoDB items",
    )
    sync.add_argument("--scan-segments", type=int, default=8)
    sync.add_argument("--consistent-read", action="store_true")
    sync.add_argument("--image-workers", type=int, default=32)
    sync.add_argument("--chroma-workers", type=int, default=16)
    sync.add_argument(
        "--refresh-images",
        action="store_true",
        help="HEAD cached images and redownload changed objects",
    )
    sync.add_argument("--force-chroma", action="store_true")

    status = subparsers.add_parser("status", help="Show cached versions and counts")
    _add_common_arguments(status)
    status.add_argument("--json", action="store_true", dest="as_json")

    validate = subparsers.add_parser(
        "validate", help="Check cache integrity and remote source versions"
    )
    _add_common_arguments(validate)
    validate.add_argument("--components", default="all")
    validate.add_argument(
        "--local-only", action="store_true", help="Skip AWS freshness checks"
    )
    validate.add_argument(
        "--deep",
        action="store_true",
        help="HEAD every raw S3 object in addition to quick checks",
    )
    validate.add_argument("--image-workers", type=int, default=32)
    validate.add_argument("--json", action="store_true", dest="as_json")

    invalidate = subparsers.add_parser(
        "invalidate", help="Instantly mark cached data invalid"
    )
    _add_common_arguments(invalidate)
    invalidate.add_argument("--components", default="all")
    invalidate.add_argument(
        "--purge", action="store_true", help="Also delete cached files"
    )
    invalidate.add_argument("--json", action="store_true", dest="as_json")

    serve = subparsers.add_parser(
        "serve", help="Start and hydrate DynamoDB Local from the cache"
    )
    _add_common_arguments(serve)
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--endpoint-url", help="Use an existing local endpoint")
    serve.add_argument("--no-docker", action="store_true")
    serve.add_argument("--force-import", action="store_true")
    serve.add_argument("--import-workers", type=int, default=16)
    serve.add_argument("--docker-image", default="amazon/dynamodb-local:latest")
    serve.add_argument("--json", action="store_true", dest="as_json")

    stop = subparsers.add_parser("stop", help="Stop the DynamoDB Local container")
    _add_common_arguments(stop)
    stop.add_argument("--port", type=int, default=8000)
    stop.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args.cache_root = args.cache_dir.expanduser().resolve() / args.env

    try:
        if args.command == "sync":
            if (
                args.scan_segments < 1
                or args.image_workers < 1
                or args.chroma_workers < 1
            ):
                parser.error("worker and segment counts must be positive")
            result = sync_cache(args)
            _print_status(result, args.cache_root)
            requested = _parse_components(args.components)
            succeeded = all(
                result.get("components", {}).get(name, {}).get("valid") is True
                for name in requested
            )
            return 0 if succeeded else 1
        if args.command == "status":
            result = _load_manifest(args.cache_root)
            if args.as_json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                _print_status(result, args.cache_root)
            return 0 if result else 1
        if args.command == "validate":
            result = validate_cache(args)
            if args.as_json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print("VALID" if result["valid"] else "INVALID")
                for error in result["errors"]:
                    print(f"error: {error}")
                for warning in result["warnings"]:
                    print(f"warning: {warning}")
            return 0 if result["valid"] else 1
        if args.command == "invalidate":
            result = invalidate_cache(
                args.cache_root, _parse_components(args.components), args.purge
            )
            if args.as_json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(
                    f"Invalidated {args.components} cache at {args.cache_root}"
                    + (" and purged files" if args.purge else "")
                )
            return 0
        if args.command == "serve":
            if args.port < 1 or args.import_workers < 1:
                parser.error("port and import worker count must be positive")
            result = serve_dynamodb_cache(args)
            if args.as_json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(
                    f"DynamoDB Local is ready at {result['endpoint_url']} "
                    f"with {result['item_count']:,} items in {result['table_name']}"
                )
                print(f"export DYNAMODB_ENDPOINT_URL={result['endpoint_url']}")
                print(f"export DYNAMODB_TABLE_NAME={result['table_name']}")
                for name, path in result["chroma"].items():
                    print(f"ChromaDB {name}: {path}")
            return 0
        if args.command == "stop":
            result = stop_dynamodb_cache(args)
            if args.as_json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                action = "Stopped" if result["stopped"] else "Not running"
                print(f"{action}: {result['container']}")
            return 0
    except (
        argparse.ArgumentTypeError,
        RuntimeError,
        BotoCoreError,
        ClientError,
        OSError,
        sqlite3.Error,
    ) as exc:
        LOG.error("%s", exc)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
