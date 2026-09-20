"""Protocol, freshness, and read-only guarantees of the email replica MCP."""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import sqlite3
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

LAMBDA_DIR = Path(__file__).parents[1] / "email_receipt_inbox" / "lambdas"
HANDLER_PATH = LAMBDA_DIR / "mcp.py"

# Minimal mirror of receipts-email/emlrec/schema.sql: only the columns the
# vendored read queries touch.
SCHEMA = """
CREATE TABLE messages (
  message_id TEXT PRIMARY KEY, content_hash TEXT UNIQUE, mbox_file TEXT,
  byte_offset INTEGER, byte_length INTEGER, from_addr TEXT, from_domain TEXT,
  date_iso TEXT, subject TEXT, grp TEXT, classification TEXT);
CREATE TABLE email_receipts (
  message_id TEXT PRIMARY KEY, grp TEXT, merchant_name TEXT,
  merchant_platform TEXT, merchant_category TEXT, date TEXT, order_id TEXT,
  grand_total_cents INTEGER, subtotal_cents INTEGER, tax_cents INTEGER,
  tip_cents INTEGER, total_kind TEXT DEFAULT 'final', payment_type TEXT,
  card_last4 TEXT, last4_kind TEXT, recon_scope TEXT DEFAULT 'chase',
  currency TEXT DEFAULT 'USD', direction TEXT DEFAULT 'outflow',
  item_count INTEGER, superseded_by TEXT, dedupe_key TEXT, parser_name TEXT,
  parser_version TEXT, confidence REAL, extra TEXT);
CREATE TABLE receipt_items (
  message_id TEXT, line_no INTEGER, description TEXT, quantity REAL,
  unit_price_cents INTEGER, total_cents INTEGER, kind TEXT DEFAULT 'item');
CREATE TABLE paper_receipts (
  image_id TEXT, receipt_id INTEGER, merchant_name TEXT,
  merchant_category TEXT, date TEXT, grand_total_cents INTEGER,
  subtotal_cents INTEGER, tax_cents INTEGER, tip_cents INTEGER,
  item_count INTEGER, snapshot_at TEXT);
CREATE TABLE paper_receipt_items (
  image_id TEXT, receipt_id INTEGER, line_id INTEGER, description TEXT,
  quantity REAL, unit_price_cents INTEGER, total_cents INTEGER);
CREATE TABLE chase_transactions (
  txn_id TEXT PRIMARY KEY, account TEXT, posting_date TEXT, txn_date TEXT,
  description TEXT, amount_cents INTEGER, type TEXT, txn_class TEXT,
  is_card_purchase INTEGER DEFAULT 0);
CREATE TABLE matches (
  txn_id TEXT, ref_kind TEXT, ref TEXT, score REAL, score_detail TEXT,
  match_kind TEXT DEFAULT 'one_to_one', status TEXT);
CREATE TABLE match_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT, ref_kind TEXT,
  ref TEXT, decision TEXT, decided_by TEXT, at TEXT);
CREATE TABLE txn_tags (txn_id TEXT PRIMARY KEY, tag TEXT, note TEXT, at TEXT);
CREATE TABLE merchant_canonical (
  raw TEXT PRIMARY KEY, canonical TEXT, category TEXT, source TEXT);
CREATE TABLE parse_failures (
  message_id TEXT PRIMARY KEY, grp TEXT, error TEXT, at TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _build_replica(tmp_path: Path, *, receipts: int = 2) -> bytes:
    path = tmp_path / "primary.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for i in range(receipts):
        mid = f"<msg{i}@doordash.com>"
        conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid,
                f"hash{i}",
                "ses/key",
                0,
                10,
                "no-reply@doordash.com",
                "doordash.com",
                "2026-07-0%d" % (i + 1),
                "Order confirmed",
                "doordash",
                "receipt",
            ),
        )
        conn.execute(
            """INSERT INTO email_receipts
               (message_id, grp, merchant_name, merchant_platform, date,
                order_id, grand_total_cents, tax_cents, tip_cents, item_count)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                "doordash",
                "Taco Stand",
                "DoorDash",
                "2026-07-0%d" % (i + 1),
                f"dd-{i}",
                2599 + i,
                150,
                300,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO receipt_items VALUES (?,?,?,?,?,?,?)",
            (mid, 1, "Milk (whole)", 1, 2599, 2599, "item"),
        )
    conn.execute(
        "INSERT INTO chase_transactions VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "txn1",
            "7739",
            "2026-07-02",
            "2026-07-01",
            "DOORDASH TACO STAND",
            -2599,
            "Sale",
            "in-person",
            1,
        ),
    )
    conn.execute(
        "INSERT INTO chase_transactions VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "txn2",
            "7739",
            "2026-07-03",
            "2026-07-03",
            "AMAZON.COM",
            -1000,
            "Sale",
            "amazon/aws",
            1,
        ),
    )
    conn.execute(
        "INSERT INTO matches VALUES (?,?,?,?,?,?,?)",
        (
            "txn1",
            "email",
            "<msg0@doordash.com>",
            0.95,
            "{}",
            "one_to_one",
            "auto",
        ),
    )
    conn.execute("INSERT INTO meta VALUES ('paper_snapshot_at', '2026-07-18')")
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
    monkeypatch.setenv("REPLICA_BUCKET", "mail-bucket")
    monkeypatch.setenv("REPLICA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("REPLICA_ETAG_CHECK_SECONDS", "0")
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service, **_kwargs: (
            fake
            if service == "s3"
            else pytest.fail(f"unexpected client: {service}")
        ),
    )
    monkeypatch.syspath_prepend(str(LAMBDA_DIR))
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


@pytest.fixture
def replica(monkeypatch, tmp_path):
    payload = _build_replica(tmp_path)
    manifest = json.dumps(
        {
            "published_at": "2026-09-01T07:00:00+00:00",
            "sha256": "abc",
            "row_counts": {"email_receipts": 2},
        }
    ).encode()
    return _load_handler(
        monkeypatch,
        tmp_path,
        {
            "replica/email_receipts.db.gz": payload,
            "replica/manifest.json": manifest,
        },
    )


def test_initialize_and_tool_list(replica) -> None:
    handler, _s3 = replica
    response = handler.lambda_handler(
        _event("initialize", params={"protocolVersion": "2025-06-18"}), None
    )
    assert response["statusCode"] == 200
    assert response["headers"]["mcp-protocol-version"] == "2025-06-18"
    result = json.loads(response["body"])["result"]
    assert result["serverInfo"]["name"] == "portfolio-email-receipts"
    assert "Read replica" in result["instructions"]

    tools = json.loads(
        handler.lambda_handler(_event("tools/list"), None)["body"]
    )["result"]["tools"]
    names = {tool["name"] for tool in tools}
    # The read subset of receipts-email/server.py, plus replica_status.
    assert names == {
        "get_email_receipt_summaries",
        "get_email_receipt",
        "search_email_receipts",
        "list_email_merchants",
        "get_spend_summary",
        "get_coverage",
        "get_unmatched",
        "ingest_status",
        "replica_status",
        "query_sql",
    }
    # No write tool is ever exposed from the replica.
    for forbidden in (
        "confirm_match",
        "reject_match",
        "mark_transaction",
        "reconcile_chase",
        "import_chase_csv",
        "ingest_mbox_index",
        "refresh_paper_snapshot",
    ):
        assert forbidden not in names


def test_summaries_come_from_the_downloaded_snapshot(replica) -> None:
    handler, s3 = replica
    result = _call(
        handler, "get_email_receipt_summaries", {"group": "doordash"}
    )
    assert result["isError"] is False
    payload = result["structuredContent"]
    assert payload["count"] == 2
    assert payload["currency"] == "USD"
    assert payload["total_spending"] == 51.99
    assert payload["by_currency"] == [
        {
            "currency": "USD",
            "count": 2,
            "total_spending": 51.99,
            "total_tax": 3.0,
            "total_tip": 6.0,
            "average_receipt": 26.0,
        }
    ]
    assert payload["summaries"][0]["merchant_name"] == "Taco Stand"
    assert payload["summaries"][0]["currency"] == "USD"
    # Cold start downloads once; the manifest is read alongside it.
    assert ("get", "replica/email_receipts.db.gz") in s3.calls
    assert ("get", "replica/manifest.json") in s3.calls


def test_warm_container_reuses_snapshot_until_etag_changes(
    replica, tmp_path
) -> None:
    handler, s3 = replica
    _call(handler, "ingest_status")
    downloads = [
        c for c in s3.calls if c == ("get", "replica/email_receipts.db.gz")
    ]
    assert len(downloads) == 1

    _call(handler, "ingest_status")
    downloads = [
        c for c in s3.calls if c == ("get", "replica/email_receipts.db.gz")
    ]
    assert len(downloads) == 1, "same ETag must not re-download"

    # Publish a new snapshot with three receipts: the next call sees it.
    (tmp_path / "v2").mkdir()
    s3.objects["replica/email_receipts.db.gz"] = _build_replica(
        tmp_path / "v2", receipts=3
    )
    payload = _call(handler, "get_email_receipt_summaries")[
        "structuredContent"
    ]
    assert payload["count"] == 3


def test_replica_status_reports_manifest_and_age(replica) -> None:
    handler, _s3 = replica
    payload = _call(handler, "replica_status")["structuredContent"]
    assert payload["role"] == "read-replica"
    assert payload["manifest"]["sha256"] == "abc"
    assert payload["replica_age_seconds"] is not None
    assert payload["row_counts"]["email_receipts"] == 2
    assert "primary" in payload["writes"]


def test_coverage_matches_primary_semantics(replica) -> None:
    handler, _s3 = replica
    rows = _call(handler, "get_coverage", {"period": "month"})[
        "structuredContent"
    ]["rows"]
    assert rows == [
        {
            "period": "2026-07",
            "account": "7739",
            "txns": 2,
            "matched_txns": 1,
            "spend": 35.99,
            "matched_spend": 25.99,
            "coverage_by_count": 0.5,
            "coverage_by_spend": 0.722,
        }
    ]


def test_query_sql_is_read_only(replica) -> None:
    handler, _s3 = replica
    ok = _call(handler, "query_sql", {"sql": "SELECT COUNT(*) FROM matches"})
    assert ok["structuredContent"]["rows"] == [[1]]

    denied = _call(handler, "query_sql", {"sql": "DELETE FROM matches"})
    assert denied["isError"] is True
    assert "read-only" in denied["structuredContent"]["error"]

    # A denied verb inside a string literal or comment is data, not a verb.
    literal = _call(
        handler,
        "query_sql",
        {
            "sql": "SELECT COUNT(*) FROM email_receipts "
            "WHERE merchant_name LIKE '%update%' -- drop nothing"
        },
    )
    assert literal["isError"] is False
    assert literal["structuredContent"]["rows"] == [[0]]

    # Even a statement that slips past the deny list cannot write: the
    # connection is opened mode=ro + immutable and query_only is set.
    sneaky = _call(
        handler,
        "query_sql",
        {"sql": "WITH x AS (SELECT 1) INSERT INTO meta VALUES ('a','b')"},
    )
    assert sneaky["isError"] is True


def test_query_sql_cannot_read_the_mailbox_index(replica) -> None:
    """The replica boundary is receipt-scoped: `messages` (every sender and
    subject in the 13-year mailbox) is not readable through raw SQL, directly,
    via join, subquery, or PRAGMA; the receipt tables still are."""
    handler, _s3 = replica
    for sql in (
        "SELECT subject FROM messages",
        "SELECT er.message_id FROM email_receipts er "
        "JOIN messages m ON m.message_id = er.message_id",
        "SELECT (SELECT COUNT(*) FROM messages)",
        "WITH m AS (SELECT * FROM messages) SELECT * FROM m",
        "SELECT * FROM pragma_table_info('messages')",
    ):
        denied = _call(handler, "query_sql", {"sql": sql})
        assert denied["isError"] is True, sql
        assert "receipt-scoped" in denied["structuredContent"]["error"], sql
    allowed = _call(
        handler,
        "query_sql",
        {
            "sql": "WITH t AS (SELECT grand_total_cents c FROM email_receipts) "
            "SELECT SUM(c) FROM t"
        },
    )
    assert allowed["structuredContent"]["rows"] == [[5199]]
    # The fixed tool still returns the receipt's own source email.
    receipt = _call(
        handler, "get_email_receipt", {"message_id": "<msg0@doordash.com>"}
    )["structuredContent"]
    assert receipt["email"]["subject"] == "Order confirmed"


def test_query_sql_blobs_and_limits_are_json_safe(replica) -> None:
    handler, _s3 = replica
    blob = _call(handler, "query_sql", {"sql": "SELECT x'0001' AS v"})
    assert blob["structuredContent"]["rows"] == [["AAE="]]
    assert json.dumps(blob)  # the response-level dumps must not raise

    negative = _call(handler, "query_sql", {"sql": "SELECT 1", "limit": -1})
    assert negative["isError"] is True
    assert "limit" in negative["structuredContent"]["error"]
    clamped = _call(handler, "get_email_receipt_summaries", {"limit": 10**9})
    assert clamped["isError"] is False
    assert len(clamped["structuredContent"]["summaries"]) == 2
    bad = _call(handler, "get_unmatched", {"limit": "many"})
    assert bad["isError"] is True


def test_enums_and_partial_ids_are_validated(replica) -> None:
    handler, _s3 = replica
    period = _call(handler, "get_spend_summary", {"period": "monthly"})
    assert period["isError"] is True
    kind = _call(handler, "get_unmatched", {"kind": "txn"})
    assert kind["isError"] is True
    # "doordash" is a fragment of both receipts' message_ids.
    ambiguous = _call(handler, "get_email_receipt", {"message_id": "doordash"})
    assert ambiguous["isError"] is True
    assert "more than one" in ambiguous["structuredContent"]["error"]
    exact = _call(handler, "get_email_receipt", {"message_id": "msg1@"})
    assert exact["structuredContent"]["message_id"] == "<msg1@doordash.com>"


def test_summary_average_ignores_unpriced_and_foreign_receipts(
    monkeypatch, tmp_path
) -> None:
    payload_path = tmp_path / "primary.db"
    conn = sqlite3.connect(payload_path)
    conn.executescript(SCHEMA)
    rows = [
        ("<a@x>", "retail", "A", "2026-07-01", 1000, "USD"),
        ("<b@x>", "retail", "B", "2026-07-02", None, "USD"),
        ("<c@x>", "retail", "C", "2026-07-03", 500000, "CRC"),
    ]
    for mid, grp, merchant, date, cents, currency in rows:
        conn.execute(
            """INSERT INTO email_receipts
               (message_id, grp, merchant_name, date, grand_total_cents,
                currency) VALUES (?,?,?,?,?,?)""",
            (mid, grp, merchant, date, cents, currency),
        )
    conn.commit()
    conn.close()
    handler, _s3 = _load_handler(
        monkeypatch,
        tmp_path,
        {
            "replica/email_receipts.db.gz": gzip.compress(
                payload_path.read_bytes()
            )
        },
    )
    summary = _call(handler, "get_email_receipt_summaries")[
        "structuredContent"
    ]
    assert summary["count"] == 3
    assert summary["currency"] == "USD"
    # 10.00 over the ONE priced USD receipt, not 10.00 / 2 or 5010 / 3.
    assert summary["total_spending"] == 10.0
    assert summary["average_receipt"] == 10.0
    assert {
        c["currency"]: c["total_spending"] for c in summary["by_currency"]
    } == {
        "USD": 10.0,
        "CRC": 5000.0,
    }
    spend = _call(handler, "get_spend_summary", {"period": "month"})[
        "structuredContent"
    ]
    assert spend["currency"] == "USD"
    assert spend["rows"] == [
        {
            "period": "2026-07",
            "email_receipts": {"n": 1, "total": 10.0},
            "paper_receipts": {"n": 0, "total": 0},
            "chase_card_spend": {"n": 0, "total": 0},
            "email_other_currencies": {"CRC": {"n": 1, "total": 5000.0}},
        }
    ]


def test_manifest_refreshes_independently_of_the_database(
    replica,
) -> None:
    handler, s3 = replica
    before = _call(handler, "replica_status")["structuredContent"]
    assert before["manifest"]["sha256"] == "abc"
    # replicate uploads the database first and the manifest second; a
    # manifest-only change must be visible without a new database ETag.
    s3.objects["replica/manifest.json"] = json.dumps(
        {"published_at": "2026-09-02T07:00:00+00:00", "sha256": "def"}
    ).encode()
    after = _call(handler, "replica_status")["structuredContent"]
    assert after["manifest"]["sha256"] == "def"
    assert after["etag"] == before["etag"]


def test_missing_replica_is_a_clear_tool_error(monkeypatch, tmp_path) -> None:
    handler, _s3 = _load_handler(monkeypatch, tmp_path, {})
    result = _call(handler, "ingest_status")
    assert result["isError"] is True
    assert "emlrec replicate" in result["structuredContent"]["error"]


def test_malformed_initialize_params_are_a_jsonrpc_error(replica) -> None:
    handler, _s3 = replica
    response = handler.lambda_handler(
        _event("initialize", params="unexpected"), None
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["error"]["code"] == -32602


def test_unknown_tool_and_non_post_are_rejected(replica) -> None:
    handler, _s3 = replica
    unknown = handler.lambda_handler(
        _event(
            "tools/call", params={"name": "confirm_match", "arguments": {}}
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


def _primary_queries_path() -> Path:
    """Where the receipts-email checkout lives. ``RECEIPTS_EMAIL_DIR`` lets a
    CI job that checks the primary out beside this repo point at it."""
    root = Path(
        os.environ.get("RECEIPTS_EMAIL_DIR") or Path.home() / "receipts-email"
    )
    return root / "emlrec" / "queries.py"


def test_vendored_queries_match_the_primary() -> None:
    """Drift guard: the vendored read module must be byte-identical to the
    primary below the docstring header. Skips only when no receipts-email
    checkout is available, and says so."""
    primary = _primary_queries_path()
    if not primary.exists():
        pytest.skip(
            f"receipts-email checkout not found at {primary.parent.parent}; "
            "set RECEIPTS_EMAIL_DIR to run the vendored-query drift guard"
        )
    vendored = (LAMBDA_DIR / "queries.py").read_text().split('"""', 2)[2]
    source = primary.read_text().split('"""', 2)[2]
    assert vendored == source, (
        "infra/email_receipt_inbox/lambdas/queries.py drifted from "
        f"{primary}; re-vendor it (see its docstring)"
    )


def test_vendored_queries_drift_guard_uses_the_env_override(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("RECEIPTS_EMAIL_DIR", str(tmp_path))
    assert _primary_queries_path() == tmp_path / "emlrec" / "queries.py"
