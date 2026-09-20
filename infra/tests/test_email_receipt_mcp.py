"""Protocol, freshness, contract, and read-only guarantees of the email MCP.

The Lambda serves only the agent-safe projection (two tables, ``spend`` and
``txn``) that ``emlrec publish-projection`` uploads. These tests build such
a file from the same contract the producer declares, prove the Lambda
accepts exactly that shape and rejects everything else, and, when a
receipts-email checkout is available, run the real exporter end to end.
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

LAMBDA_DIR = Path(__file__).parents[1] / "email_receipt_inbox" / "lambdas"
HANDLER_PATH = LAMBDA_DIR / "mcp.py"

# The producer contract (receipts-email/emlrec/projection.py SCHEMA), version 2.
PROJECTION_SCHEMA = """
CREATE TABLE spend (
    source TEXT, date TEXT, merchant_name TEXT, merchant_category TEXT,
    item_description TEXT, quantity REAL, unit_price_cents INTEGER,
    total_cents INTEGER, receipt_total_cents INTEGER, receipt_ref TEXT,
    currency TEXT
);
CREATE TABLE txn (
    txn_date TEXT, posting_date TEXT, merchant_canonical TEXT, category TEXT,
    amount_cents INTEGER, txn_class TEXT, is_card_purchase INTEGER,
    currency TEXT
);
PRAGMA user_version = 2;
"""

SPEND_ROWS = [
    # Two items of one email receipt: receipt_total_cents repeats.
    (
        "email",
        "2026-07-01",
        "Taco Stand",
        "restaurants",
        "Taco",
        2,
        600,
        1200,
        2599,
        "a1b2c3d4e5f6",
        "USD",
    ),
    (
        "email",
        "2026-07-01",
        "Taco Stand",
        "restaurants",
        "Horchata",
        1,
        1399,
        1399,
        2599,
        "a1b2c3d4e5f6",
        "USD",
    ),
    # A paper receipt: currency unknown, never assumed USD.
    (
        "paper",
        "2026-07-02",
        "Sprouts",
        "grocery",
        "Milk (whole)",
        1,
        499,
        499,
        499,
        "img1:1",
        None,
    ),
    # A non-USD receipt keeps its currency.
    (
        "email",
        "2026-07-03",
        "Volcano Cafe",
        "restaurants",
        "Casado",
        1,
        500000,
        500000,
        500000,
        "0f0f0f0f0f0f",
        "CRC",
    ),
]
TXN_ROWS = [
    (
        "2026-07-01",
        "2026-07-02",
        "Taco Stand",
        "restaurants",
        -2599,
        "in-person",
        1,
        "USD",
    ),
    ("2026-07-03", "2026-07-03", None, None, -1000, "amazon/aws", 1, "USD"),
]


def _build_projection(path: Path, *, extra_sql: str = "") -> bytes:
    conn = sqlite3.connect(path)
    conn.executescript(PROJECTION_SCHEMA)
    conn.executemany(
        "INSERT INTO spend VALUES (?,?,?,?,?,?,?,?,?,?,?)", SPEND_ROWS
    )
    conn.executemany("INSERT INTO txn VALUES (?,?,?,?,?,?,?,?)", TXN_ROWS)
    if extra_sql:
        conn.executescript(extra_sql)
    conn.commit()
    conn.close()
    return gzip.compress(path.read_bytes())


class FakeS3:
    """Just enough of boto3's S3 client for the handler."""

    class exceptions:  # noqa: D106 - mirrors boto3's nested namespace
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.calls: list[tuple[str, str]] = []

    def _etag(self, key: str) -> str:
        import hashlib

        return '"' + hashlib.md5(self.objects[key]).hexdigest() + '"'

    def head_object(self, *, Bucket: str, Key: str):
        self.calls.append(("head", Key))
        if Key not in self.objects:
            # Real S3 reports a missing key on HEAD as a bare 404 ClientError,
            # not the modelled NoSuchKey that GetObject raises.
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {
            "ETag": self._etag(Key),
            "ContentLength": len(self.objects[Key]),
        }

    def get_object(self, *, Bucket: str, Key: str):
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(self.objects[Key])}


def _load_handler(monkeypatch, tmp_path: Path, objects: dict[str, bytes]):
    fake = FakeS3(objects)
    monkeypatch.setenv("PROJECTION_BUCKET", "mail-bucket")
    monkeypatch.setenv("PROJECTION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PROJECTION_ETAG_CHECK_SECONDS", "0")
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service, **_kwargs: (
            fake
            if service == "s3"
            else pytest.fail(f"unexpected client: {service}")
        ),
    )
    spec = importlib.util.spec_from_file_location(
        "email_receipt_mcp", HANDLER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, fake


def _event(method: str, request_id=1, params=None) -> dict:
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(request),
    }


def _call(handler, name: str, arguments: dict | None = None) -> dict:
    response = handler.lambda_handler(
        _event(
            "tools/call", params={"name": name, "arguments": arguments or {}}
        ),
        None,
    )
    assert response["statusCode"] == 200
    return json.loads(response["body"])["result"]


def _sql(handler, sql: str, **extra) -> dict:
    return _call(handler, "query_sql", {"sql": sql, **extra})


MANIFEST = {
    "published_at": "2026-09-01T07:00:00+00:00",
    "schema_version": 2,
    "sha256": "abc",
    "row_counts": {"spend": 4, "txn": 2},
}


@pytest.fixture
def projection(monkeypatch, tmp_path):
    payload = _build_projection(tmp_path / "spend.db")
    return _load_handler(
        monkeypatch,
        tmp_path,
        {
            "agent/spend.db.gz": payload,
            "agent/manifest.json": json.dumps(MANIFEST).encode(),
        },
    )


def test_initialize_and_tool_list(projection) -> None:
    handler, _s3 = projection
    response = handler.lambda_handler(
        _event("initialize", params={"protocolVersion": "2025-06-18"}), None
    )
    assert response["statusCode"] == 200
    assert response["headers"]["mcp-protocol-version"] == "2025-06-18"
    result = json.loads(response["body"])["result"]
    assert result["serverInfo"]["name"] == "portfolio-email-receipts"
    assert "projection" in result["instructions"]

    tools = json.loads(
        handler.lambda_handler(_event("tools/list"), None)["body"]
    )["result"]["tools"]
    names = {tool["name"] for tool in tools}
    # Exactly the projection surface; the legacy receipt-detail, coverage,
    # unmatched, and ingestion tools are deferred, and no write tool exists.
    assert names == {"query_sql", "replica_status"}
    for forbidden in (
        "get_email_receipt",
        "get_email_receipt_summaries",
        "search_email_receipts",
        "get_coverage",
        "get_unmatched",
        "ingest_status",
        "confirm_match",
        "reject_match",
        "mark_transaction",
        "reconcile_chase",
        "import_chase_csv",
        "ingest_mbox_index",
    ):
        assert forbidden not in names
    query_tool = next(t for t in tools if t["name"] == "query_sql")
    assert "receipt_total_cents repeats" in query_tool["description"]
    assert query_tool["inputSchema"]["properties"]["limit"]["maximum"] == 1000


def test_query_sql_answers_from_the_downloaded_projection(projection) -> None:
    handler, s3 = projection
    result = _sql(
        handler,
        "SELECT currency, SUM(total_cents) FROM spend "
        "GROUP BY currency ORDER BY currency",
    )
    assert result["isError"] is False
    # Currencies are never added together; NULL (paper) stays its own group.
    assert result["structuredContent"]["rows"] == [
        [None, 499],
        ["CRC", 500000],
        ["USD", 2599],
    ]
    assert ("get", "agent/spend.db.gz") in s3.calls
    assert ("get", "agent/manifest.json") in s3.calls

    receipts = _sql(
        handler,
        "SELECT DISTINCT receipt_ref, receipt_total_cents FROM spend "
        "WHERE currency = 'USD'",
    )["structuredContent"]
    assert receipts["rows"] == [["a1b2c3d4e5f6", 2599]]


def test_warm_container_reuses_snapshot_until_etag_changes(
    projection, tmp_path
) -> None:
    handler, s3 = projection
    _call(handler, "replica_status")
    downloads = [c for c in s3.calls if c == ("get", "agent/spend.db.gz")]
    assert len(downloads) == 1
    _call(handler, "replica_status")
    downloads = [c for c in s3.calls if c == ("get", "agent/spend.db.gz")]
    assert len(downloads) == 1, "same ETag must not re-download"

    s3.objects["agent/spend.db.gz"] = _build_projection(
        tmp_path / "v2.db",
        extra_sql="DELETE FROM txn WHERE amount_cents = -1000",
    )
    rows = _sql(handler, "SELECT COUNT(*) FROM txn")["structuredContent"]
    assert rows["rows"] == [[1]]


def test_replica_status_reports_manifest_schema_and_age(projection) -> None:
    handler, _s3 = projection
    payload = _call(handler, "replica_status")["structuredContent"]
    assert payload["role"].startswith("agent-safe projection")
    assert payload["manifest"]["sha256"] == "abc"
    assert payload["replica_age_seconds"] is not None
    assert payload["schema_version"] == 2
    assert payload["row_counts"] == {"spend": 4, "txn": 2}
    assert payload["currencies"] == {
        "spend": {"CRC": 1, "USD": 2, "unknown": 1},
        "txn": {"USD": 2},
    }
    assert payload["tables"]["spend"][-1] == "currency"
    assert "primary" in payload["writes"]


def test_manifest_refreshes_independently_of_the_database(projection) -> None:
    handler, s3 = projection
    before = _call(handler, "replica_status")["structuredContent"]
    s3.objects["agent/manifest.json"] = json.dumps(
        {**MANIFEST, "sha256": "def"}
    ).encode()
    after = _call(handler, "replica_status")["structuredContent"]
    assert after["manifest"]["sha256"] == "def"
    assert after["etag"] == before["etag"]


def test_query_sql_is_read_only_and_literal_safe(projection) -> None:
    handler, _s3 = projection
    for sql in (
        "DELETE FROM txn",
        "WITH x AS (SELECT 1) INSERT INTO txn VALUES (1,2,3,4,5,6,7,8)",
        "PRAGMA user_version = 9",
        "ATTACH ':memory:' AS other",
        "SELECT 1; DROP TABLE spend",
    ):
        denied = _sql(handler, sql)
        assert denied["isError"] is True, sql
    literal = _sql(
        handler,
        "SELECT COUNT(*) FROM spend WHERE item_description LIKE '%update%' "
        "-- drop nothing",
    )
    assert literal["isError"] is False
    assert literal["structuredContent"]["rows"] == [[0]]


def test_query_sql_cannot_read_anything_but_spend_and_txn(projection) -> None:
    handler, _s3 = projection
    for sql in (
        "SELECT name FROM sqlite_master",
        "SELECT * FROM sqlite_schema",
        "SELECT * FROM pragma_table_info('spend')",
    ):
        denied = _sql(handler, sql)
        assert denied["isError"] is True, sql
        assert "spend and txn" in denied["structuredContent"]["error"], sql
    # The mailbox index does not exist in the projection at all.
    absent = _sql(handler, "SELECT * FROM messages")
    assert absent["isError"] is True
    assert "no such table" in absent["structuredContent"]["error"]
    allowed = _sql(
        handler,
        "WITH t AS (SELECT amount_cents c FROM txn WHERE is_card_purchase = 1) "
        "SELECT SUM(c) FROM t",
    )
    assert allowed["structuredContent"]["rows"] == [[-3599]]


def test_query_sql_bounds_rows_and_response_size(
    projection, monkeypatch
) -> None:
    handler, _s3 = projection
    negative = _sql(handler, "SELECT 1", limit=-1)
    assert negative["isError"] is True
    assert "limit" in negative["structuredContent"]["error"]
    bad = _sql(handler, "SELECT 1", limit="many")
    assert bad["isError"] is True
    capped = _sql(handler, "SELECT * FROM spend", limit=10**9)
    assert capped["isError"] is False
    assert capped["structuredContent"]["row_count"] == 4
    two = _sql(handler, "SELECT * FROM spend ORDER BY date", limit=2)
    assert two["structuredContent"]["row_count"] == 2
    assert two["structuredContent"]["truncated"] is True

    monkeypatch.setattr(handler, "MAX_RESPONSE_BYTES", 200)
    shed = _sql(handler, "SELECT * FROM spend")["structuredContent"]
    assert shed["truncated"] is True
    assert 1 <= shed["row_count"] < 4
    monkeypatch.setattr(handler, "MAX_RESPONSE_BYTES", 10)
    too_big = _sql(handler, "SELECT * FROM spend")
    assert too_big["isError"] is True
    assert "single row" in too_big["structuredContent"]["error"]


def test_values_are_json_safe(projection) -> None:
    handler, _s3 = projection
    blob = _sql(handler, "SELECT x'0001' AS v, 12.5 AS f, NULL AS n")
    assert blob["structuredContent"]["rows"] == [["AAE=", 12.5, None]]
    assert json.dumps(blob)  # the response-level dumps must not raise
    cents = _sql(handler, "SELECT total_cents FROM spend WHERE currency='CRC'")
    assert cents["structuredContent"]["rows"] == [[500000]]
    assert isinstance(cents["structuredContent"]["rows"][0][0], int)


@pytest.mark.parametrize(
    "extra_sql, reason",
    [
        ("CREATE TABLE messages (subject TEXT);", "exactly the spend and txn"),
        ("CREATE VIEW v AS SELECT * FROM spend;", "views or triggers"),
        (
            "CREATE TRIGGER t AFTER INSERT ON spend BEGIN SELECT 1; END;",
            "views or triggers",
        ),
        ("ALTER TABLE spend ADD COLUMN card_last4 TEXT;", "forbidden column"),
        ("ALTER TABLE txn ADD COLUMN note TEXT;", "unexpected columns"),
        ("ALTER TABLE spend DROP COLUMN currency;", "unexpected columns"),
        ("PRAGMA user_version = 1;", "schema version 1"),
    ],
)
def test_invalid_projection_is_rejected_and_never_served(
    monkeypatch, tmp_path, extra_sql, reason
) -> None:
    payload = _build_projection(tmp_path / "bad.db", extra_sql=extra_sql)
    handler, _s3 = _load_handler(
        monkeypatch, tmp_path, {"agent/spend.db.gz": payload}
    )
    result = _sql(handler, "SELECT COUNT(*) FROM spend")
    assert result["isError"] is True
    assert "rejected" in result["structuredContent"]["error"]
    assert reason in result["structuredContent"]["error"]
    # Nothing is served: even replica_status fails closed.
    status = _call(handler, "replica_status")
    assert status["isError"] is True


def test_a_bad_publish_does_not_keep_serving_the_previous_file(
    projection, tmp_path
) -> None:
    handler, s3 = projection
    assert _sql(handler, "SELECT COUNT(*) FROM spend")["isError"] is False
    s3.objects["agent/spend.db.gz"] = _build_projection(
        tmp_path / "bad.db", extra_sql="CREATE TABLE messages (subject TEXT);"
    )
    assert _sql(handler, "SELECT COUNT(*) FROM spend")["isError"] is True
    assert _call(handler, "replica_status")["isError"] is True


def test_missing_projection_is_a_clear_tool_error(
    monkeypatch, tmp_path
) -> None:
    handler, _s3 = _load_handler(monkeypatch, tmp_path, {})
    result = _call(handler, "replica_status")
    assert result["isError"] is True
    assert "publish-projection" in result["structuredContent"]["error"]


def test_malformed_initialize_params_are_a_jsonrpc_error(projection) -> None:
    handler, _s3 = projection
    response = handler.lambda_handler(
        _event("initialize", params="unexpected"), None
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["error"]["code"] == -32602


def test_unknown_tool_and_non_post_are_rejected(projection) -> None:
    handler, _s3 = projection
    unknown = handler.lambda_handler(
        _event(
            "tools/call", params={"name": "get_email_receipt", "arguments": {}}
        ),
        None,
    )
    assert json.loads(unknown["body"])["error"]["code"] == -32602
    get = _event("ping")
    get["requestContext"]["http"]["method"] = "GET"
    assert handler.lambda_handler(get, None)["statusCode"] == 405


def test_origin_is_validated_when_browser_supplies_it(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://claude.ai")
    handler, _s3 = _load_handler(monkeypatch, tmp_path, {})
    allowed = _event("ping")
    allowed["headers"] = {"Origin": "https://claude.ai"}
    blocked = _event("ping")
    blocked["headers"] = {"origin": "https://attacker.example"}
    assert handler.lambda_handler(allowed, None)["statusCode"] == 200
    assert handler.lambda_handler(blocked, None)["statusCode"] == 403


# ---------------------------------------------------------------------------
# Producer/consumer contract against the real exporter
# ---------------------------------------------------------------------------
def _receipts_email_dir() -> Path:
    """Where the receipts-email checkout lives. ``RECEIPTS_EMAIL_DIR`` lets a
    CI job that checks the producer out beside this repo point at it."""
    return Path(
        os.environ.get("RECEIPTS_EMAIL_DIR") or Path.home() / "receipts-email"
    )


def _import_producer():
    root = _receipts_email_dir()
    if not (root / "emlrec" / "projection.py").exists():
        pytest.skip(
            f"receipts-email checkout not found at {root}; set "
            "RECEIPTS_EMAIL_DIR to run the producer/consumer contract test"
        )
    sys.path.insert(0, str(root))
    try:
        from emlrec import (
            projection as producer,  # type: ignore[import-not-found]
        )
    finally:
        sys.path.remove(str(root))
    return root, producer


def test_producer_contract_matches_consumer_constants() -> None:
    _root, producer = _import_producer()
    handler_source = HANDLER_PATH.read_text()
    spec = importlib.util.spec_from_file_location("mcp_contract", HANDLER_PATH)
    # Import for the constants only (no S3 client is needed to read them).
    os.environ.setdefault("PROJECTION_BUCKET", "contract-check")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert module.SCHEMA_VERSION == producer.SCHEMA_VERSION
    assert module.COLUMNS == producer.COLUMNS
    assert module.FORBIDDEN_COLUMNS == producer.FORBIDDEN_COLUMNS
    assert "PROJECTION_DB_KEY" in handler_source
    assert producer.DB_NAME == "spend.db.gz"
    assert producer.MANIFEST_NAME == "manifest.json"


def test_real_exporter_output_is_accepted_and_served(
    monkeypatch, tmp_path
) -> None:
    """Producer writes schema X from a synthetic primary; the Lambda accepts
    exactly X and answers from it. No owner database is opened."""
    root, producer = _import_producer()
    schema = (root / "emlrec" / "schema.sql").read_text()
    primary = tmp_path / "primary.db"
    conn = sqlite3.connect(primary)
    conn.executescript(schema)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS merchant_canonical (
            raw TEXT PRIMARY KEY, canonical TEXT NOT NULL, category TEXT,
            source TEXT);
        CREATE TABLE IF NOT EXISTS paper_receipt_items (
            image_id TEXT NOT NULL, receipt_id INTEGER NOT NULL,
            line_id INTEGER NOT NULL, description TEXT, quantity REAL,
            unit_price_cents INTEGER, total_cents INTEGER,
            PRIMARY KEY (image_id, receipt_id, line_id));
        """)
    conn.execute(
        "INSERT INTO messages (message_id, mbox_file, byte_offset, byte_length,"
        " subject, from_addr) VALUES ('<m1@x>', 'synthetic', 0, 0,"
        " 'SECRET SUBJECT', 'secret@example.invalid')"
    )
    conn.execute("""INSERT INTO email_receipts
           (message_id, grp, merchant_name, date, grand_total_cents, currency,
            card_last4, order_id)
           VALUES ('<m1@x>', 'synthetic', 'Taco Stand', '2026-07-01', 2599,
                   'CRC', '4242', 'SECRET-ORDER')""")
    conn.execute(
        """INSERT INTO receipt_items (message_id, line_no, description,
           quantity, unit_price_cents, total_cents)
           VALUES ('<m1@x>', 1, 'Taco', 1, 2599, 2599)"""
    )
    conn.execute(
        """INSERT INTO chase_transactions (txn_id, account, posting_date,
           txn_date, description, amount_cents, txn_class, is_card_purchase)
           VALUES ('t1', 'SECRET-ACCOUNT', '2026-07-02', '2026-07-01',
                   'SECRET DESCRIPTOR', -2599, 'in-person', 1)"""
    )
    conn.commit()
    conn.close()

    exported = tmp_path / "spend.db"
    producer.export_projection(exported, primary)
    handler, _s3 = _load_handler(
        monkeypatch,
        tmp_path,
        {"agent/spend.db.gz": gzip.compress(exported.read_bytes())},
    )
    status = _call(handler, "replica_status")["structuredContent"]
    assert status["schema_version"] == producer.SCHEMA_VERSION
    assert status["row_counts"] == {"spend": 1, "txn": 1}
    rows = _sql(
        handler, "SELECT merchant_name, total_cents, currency FROM spend"
    )
    assert rows["structuredContent"]["rows"] == [["Taco Stand", 2599, "CRC"]]
    everything = json.dumps(
        _sql(handler, "SELECT * FROM spend")["structuredContent"]
    ) + json.dumps(_sql(handler, "SELECT * FROM txn")["structuredContent"])
    for sentinel in ("SECRET", "4242", "<m1@x>", "secret@example.invalid"):
        assert sentinel not in everything
