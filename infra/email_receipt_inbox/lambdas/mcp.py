"""Read-replica MCP server for email receipts.

The SQLite file on the Mac (``~/receipts-email/email_receipts.db``) is the
primary. ``emlrec replicate`` uploads a ``VACUUM INTO`` snapshot, gzipped,
plus a manifest to ``s3://<mail bucket>/replica/``. This Lambda downloads
the snapshot on cold start, re-checks the object ETag at most once a minute,
opens it read-only, and answers the same read tools the local stdio server
(``receipts-email/server.py``) answers. Writes (confirm/reject a match, tag a
transaction, ingest) are not exposed: they happen on the primary and arrive
here with the next replica.

Transport: stateless MCP Streamable HTTP behind the shared Cognito gateway
(``/email/mcp``). No dependencies beyond the Lambda runtime (boto3, sqlite3).
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import sqlite3
import time

import boto3
import queries
from botocore.exceptions import ClientError

BUCKET = os.environ["REPLICA_BUCKET"]
DB_KEY = os.environ.get("REPLICA_DB_KEY", "replica/email_receipts.db.gz")
MANIFEST_KEY = os.environ.get("REPLICA_MANIFEST_KEY", "replica/manifest.json")
CACHE_DIR = os.environ.get("REPLICA_CACHE_DIR", "/tmp/email-replica")
# How long a warm container trusts its cached ETag before HEADing S3 again.
ETAG_CHECK_SECONDS = int(os.environ.get("REPLICA_ETAG_CHECK_SECONDS", "60"))
# Budget for a single query_sql statement; the gateway integration window is
# 29s and the function timeout is 25s, so abort well before either.
SQL_BUDGET_SECONDS = float(os.environ.get("REPLICA_SQL_BUDGET_SECONDS", "10"))
# Hard ceiling on any per-call row limit; the fixed tools' schema defaults
# stay well below it. Negative or non-integer limits are rejected outright.
MAX_LIMIT = int(os.environ.get("REPLICA_MAX_LIMIT", "1000"))
# query_sql may only read these tables. `messages` (the 13-year mailbox
# index: every sender, subject, and mbox offset) is deliberately absent: the
# replica's boundary is receipt-scoped data, and the fixed tools already
# expose the one message row that belongs to a receipt.
SQL_ALLOWED_TABLES = frozenset(
    {
        "email_receipts",
        "receipt_items",
        "paper_receipts",
        "paper_receipt_items",
        "chase_transactions",
        "matches",
        "match_overrides",
        "txn_tags",
        "merchant_canonical",
        "parse_failures",
        "meta",
        # Schema discovery for the allowed tables.
        "sqlite_master",
        "sqlite_schema",
    }
)
ALLOWED_ORIGINS = {
    value.strip()
    for value in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if value.strip()
}

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, "2024-11-05", "2025-03-26"}
SERVER_INFO = {"name": "portfolio-email-receipts", "version": "1.0.0"}

s3 = boto3.client("s3")

_state: dict = {
    "etag": None,
    "conn": None,
    "checked_at": 0.0,
    "manifest": None,
    "manifest_etag": None,
    "loaded_at": None,
}


class ReplicaMissing(Exception):
    """The snapshot has never been published (or was deleted)."""


def _head(key: str) -> dict | None:
    """HEAD an object; None when it does not exist.

    ``head_object`` reports a missing key as a generic ``ClientError`` with a
    404 rather than the modelled ``NoSuchKey`` that ``get_object`` raises.
    """
    try:
        return s3.head_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.NoSuchKey:
        return None
    except ClientError as exc:
        error = exc.response.get("Error", {})
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404 or error.get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return None
        raise


# ---------------------------------------------------------------------------
# Replica lifecycle
# ---------------------------------------------------------------------------
def _read_manifest() -> dict | None:
    try:
        body = s3.get_object(Bucket=BUCKET, Key=MANIFEST_KEY)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None
    try:
        manifest = json.loads(body)
    except (ValueError, UnicodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _refresh_manifest() -> None:
    """Track the manifest by its own ETag.

    ``emlrec replicate`` uploads the database and the manifest as two PUTs,
    so a request between them must not pin a stale manifest to the new
    database for the rest of the replication interval.
    """
    head = _head(MANIFEST_KEY)
    if head is None:
        _state.update(manifest=None, manifest_etag=None)
        return
    etag = head["ETag"].strip('"')
    if etag == _state["manifest_etag"] and _state["manifest"] is not None:
        return
    _state.update(manifest=_read_manifest(), manifest_etag=etag)


def _download(etag: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{etag}.db")
    if os.path.exists(path):
        return path
    part = path + ".part"
    body = s3.get_object(Bucket=BUCKET, Key=DB_KEY)["Body"]
    with gzip.GzipFile(fileobj=body) as gz, open(part, "wb") as out:
        shutil.copyfileobj(gz, out)
    os.replace(part, path)
    # Keep /tmp bounded: drop snapshots other than the one just written.
    for name in os.listdir(CACHE_DIR):
        if name.endswith(".db") and name != f"{etag}.db":
            try:
                os.unlink(os.path.join(CACHE_DIR, name))
            except OSError:
                pass
    return path


def _connection() -> sqlite3.Connection:
    """Return a read-only connection to the freshest replica."""
    now = time.monotonic()
    if (
        _state["conn"] is not None
        and now - _state["checked_at"] < ETAG_CHECK_SECONDS
    ):
        return _state["conn"]
    head = _head(DB_KEY)
    if head is None:
        raise ReplicaMissing(DB_KEY)
    etag = head["ETag"].strip('"')
    _state["checked_at"] = now
    _refresh_manifest()
    if etag == _state["etag"] and _state["conn"] is not None:
        return _state["conn"]
    path = _download(etag)
    conn = sqlite3.connect(
        f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    previous = _state["conn"]
    _state.update(etag=etag, conn=conn, loaded_at=time.time())
    if previous is not None:
        previous.close()
    return conn


def _replica_status(conn: sqlite3.Connection) -> dict:
    manifest = _state["manifest"] or {}
    published_at = manifest.get("published_at")
    age_seconds = None
    if isinstance(published_at, str):
        try:
            from datetime import datetime, timezone

            published = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age_seconds = int(
                (datetime.now(timezone.utc) - published).total_seconds()
            )
        except ValueError:
            age_seconds = None
    counts = {}
    for table in (
        "messages",
        "email_receipts",
        "receipt_items",
        "paper_receipts",
        "chase_transactions",
        "matches",
    ):
        try:
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        except sqlite3.Error:
            counts[table] = None
    return {
        "role": "read-replica",
        "primary": "~/receipts-email/email_receipts.db on the Mac",
        "bucket": BUCKET,
        "key": DB_KEY,
        "etag": _state["etag"],
        "loaded_at": _state["loaded_at"],
        "manifest": manifest,
        "replica_age_seconds": age_seconds,
        "row_counts": counts,
        "writes": "not available here — confirm/reject/mark/ingest run on the "
        "primary and land with the next replicate",
    }


class _QueryBudgetExceeded(Exception):
    pass


_SQL_READ_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}


def _sql_authorizer(action, table, _column, _db, _trigger):
    """SQLite authorizer for query_sql: reads of allow-listed tables only.

    Every other action (writes, PRAGMA, ATTACH, transactions, reads of
    ``messages`` or any future table) fails at prepare time with
    "not authorized", independent of the keyword denylist in
    :func:`queries.query_sql`.
    """
    if action in _SQL_READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and table in SQL_ALLOWED_TABLES:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _guarded_query_sql(conn: sqlite3.Connection, sql: str, limit: int) -> dict:
    deadline = time.monotonic() + SQL_BUDGET_SECONDS

    def _check():
        if time.monotonic() > deadline:
            return 1  # non-zero aborts the statement
        return 0

    conn.set_progress_handler(_check, 10_000)
    conn.set_authorizer(_sql_authorizer)
    try:
        return queries.query_sql(conn, sql, limit=limit)
    except sqlite3.DatabaseError as exc:
        text = str(exc)
        lowered = text.lower()
        if "interrupted" in lowered:
            return {
                "error": f"query exceeded {SQL_BUDGET_SECONDS:.0f}s budget"
            }
        if "not authorized" in lowered or "prohibited" in lowered:
            return {
                "error": (
                    f"{text}. query_sql is read-only over receipt-scoped "
                    "tables: " + ", ".join(sorted(SQL_ALLOWED_TABLES))
                )
            }
        return {"error": f"sqlite error: {text}"}
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)


def _bounded(a: dict, key: str, default: int, *, minimum: int = 1) -> int:
    """Integer argument clamped to ``[minimum, MAX_LIMIT]``.

    SQLite reads ``LIMIT -1`` as "no limit", so negatives are rejected
    rather than clamped; oversize values are clamped so a single call can
    never serialize the whole replica.
    """
    raw = a.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"{key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{key} must be >= 0")
    return max(minimum, min(value, MAX_LIMIT))


# ---------------------------------------------------------------------------
# Tool surface (names and shapes mirror receipts-email/server.py)
# ---------------------------------------------------------------------------
_DATE_RANGE = {
    "start_date": {"type": "string", "description": "ISO date, inclusive"},
    "end_date": {"type": "string", "description": "ISO date, inclusive"},
}

TOOLS = [
    {
        "name": "get_email_receipt_summaries",
        "description": (
            "Pre-computed email-receipt summaries with totals/tax/tip, "
            "filterable by merchant (partial match), group (apple|doordash|"
            "amazon|venmo|paypal|pos-restaurants|uber|travel-housing|retail|"
            "services|equinox|sce|restaurant-platforms|costco-warehouse|"
            "github), and ISO date range. Returns aggregates AND individual "
            "receipts, mirroring the paper receipt-tools "
            "get_receipt_summaries shape."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "merchant_filter": {"type": "string"},
                "group": {"type": "string"},
                **_DATE_RANGE,
                "min_total": {"type": "number"},
                "max_total": {"type": "number"},
                "include_superseded": {"type": "boolean", "default": False},
                "include_inflows": {"type": "boolean", "default": False},
                "currency": {
                    "type": "string",
                    "default": "USD",
                    "description": (
                        "Currency the top-level totals cover; every "
                        "matching currency is broken out in by_currency."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 200,
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                },
                "offset": {"type": "integer", "default": 0, "minimum": 0},
            },
        },
    },
    {
        "name": "get_email_receipt",
        "description": (
            "Full detail for one email receipt: line items, payment, source "
            "email ref, and any Chase matches. Accepts full or partial "
            "message_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "search_email_receipts",
        "description": (
            "Search email AND paper receipts by merchant, order id, item "
            "description, or memo text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "default": 25,
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_email_merchants",
        "description": (
            "Merchants with receipt counts. source='email'|'paper'|'both' "
            "unions the paper-receipt snapshot for cross-source comparison."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {"type": "string"},
                "min_count": {"type": "integer", "default": 1},
                "source": {"type": "string", "default": "email"},
            },
        },
    },
    {
        "name": "get_spend_summary",
        "description": (
            "Unified spend by month (or year) across three sources: email "
            "receipts, paper receipts, and Chase card transactions. THE tool "
            "for 'how much did I spend' questions spanning sources. Totals "
            "are USD; non-USD email receipts are listed per period under "
            "email_other_currencies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_DATE_RANGE,
                "period": {
                    "type": "string",
                    "enum": list(queries.PERIODS),
                    "default": "month",
                },
            },
        },
    },
    {
        "name": "get_coverage",
        "description": (
            "Headline metric: % of Chase card spend covered by a matched "
            "receipt (email or paper), by period and account. "
            "receiptable_only=true limits to in-person store/restaurant "
            "purchases. Excludes txns tagged dad/ignored/cash."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": list(queries.PERIODS),
                    "default": "month",
                },
                "account": {"type": "string"},
                "receiptable_only": {"type": "boolean", "default": False},
                **_DATE_RANGE,
            },
        },
    },
    {
        "name": "get_unmatched",
        "description": (
            "Worklist: kind='txns' = card purchases with no matched receipt; "
            "kind='email_receipts' = email receipts with no matched Chase "
            "txn. Read-only here; confirm/reject on the primary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(queries.UNMATCHED_KINDS),
                    "default": "txns",
                },
                "account": {"type": "string"},
                **_DATE_RANGE,
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                },
            },
        },
    },
    {
        "name": "ingest_status",
        "description": (
            "Counts by group/classification, receipts per group with date "
            "ranges, table sizes, snapshot age — as of the replica."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "replica_status",
        "description": (
            "How fresh this read replica is: manifest (published_at, sha256, "
            "row counts), S3 ETag, age in seconds, and which operations are "
            "only available on the primary."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_sql",
        "description": (
            "Read-only SQL (SELECT/WITH only, 500-row cap, 10s budget) over "
            "the receipt-scoped tables of the replica: email_receipts "
            "(money in *_cents, currency column), receipt_items, "
            "paper_receipts, paper_receipt_items, chase_transactions, "
            "matches, match_overrides, txn_tags, merchant_canonical, "
            "parse_failures, meta. The mailbox index (messages) is not "
            "readable here; get_email_receipt returns the source email of a "
            "receipt. Escape hatch for anything the fixed tools don't cover."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "default": 500,
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                },
            },
            "required": ["sql"],
        },
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _call_tool(name: str, a: dict) -> dict:
    conn = _connection()
    if name == "get_email_receipt_summaries":
        return queries.email_receipt_summaries(
            conn,
            merchant=a.get("merchant_filter"),
            grp=a.get("group"),
            start_date=a.get("start_date"),
            end_date=a.get("end_date"),
            min_total=a.get("min_total"),
            max_total=a.get("max_total"),
            include_superseded=bool(a.get("include_superseded", False)),
            include_inflows=bool(a.get("include_inflows", False)),
            limit=_bounded(a, "limit", 200),
            offset=_bounded(a, "offset", 0, minimum=0),
            currency=str(a.get("currency", "USD")),
        )
    if name == "get_email_receipt":
        return queries.email_receipt(conn, str(a["message_id"]))
    if name == "search_email_receipts":
        return queries.search_receipts(
            conn, str(a["query"]), _bounded(a, "limit", 25)
        )
    if name == "list_email_merchants":
        return queries.list_merchants(
            conn,
            grp=a.get("group"),
            min_count=int(a.get("min_count", 1)),
            source=a.get("source", "email"),
        )
    if name == "get_spend_summary":
        return queries.spend_summary(
            conn,
            a.get("start_date"),
            a.get("end_date"),
            a.get("period", "month"),
        )
    if name == "get_coverage":
        return queries.coverage(
            conn,
            period=a.get("period", "month"),
            account=a.get("account"),
            receiptable_only=bool(a.get("receiptable_only", False)),
            start_date=a.get("start_date"),
            end_date=a.get("end_date"),
        )
    if name == "get_unmatched":
        return queries.unmatched(
            conn,
            kind=a.get("kind", "txns"),
            account=a.get("account"),
            start_date=a.get("start_date"),
            end_date=a.get("end_date"),
            limit=_bounded(a, "limit", 50),
        )
    if name == "ingest_status":
        return queries.ingest_status(conn)
    if name == "replica_status":
        return _replica_status(conn)
    if name == "query_sql":
        return _guarded_query_sql(
            conn, str(a["sql"]), _bounded(a, "limit", 500)
        )
    raise KeyError(name)


# ---------------------------------------------------------------------------
# JSON-RPC over HTTP API v2 (stateless Streamable HTTP)
# ---------------------------------------------------------------------------
def _response(status: int, body=None, *, protocol_version: str | None = None):
    headers = {
        "content-type": "application/json",
        "cache-control": "no-store",
    }
    if protocol_version:
        headers["mcp-protocol-version"] = protocol_version
    return {
        "statusCode": status,
        "headers": headers,
        "body": (
            "" if body is None else json.dumps(body, separators=(",", ":"))
        ),
    }


def _result(request_id, result, *, protocol_version: str | None = None):
    return _response(
        200,
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        protocol_version=protocol_version,
    )


def _error(request_id, code: int, message: str, *, status: int = 200):
    return _response(
        status,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
    )


def _json_safe(value):
    """Coerce SQLite values into JSON: BLOBs become base64 strings.

    ``structuredContent`` is serialized by the response-level ``json.dumps``
    which has no ``default=`` hook, so a ``SELECT x'00'`` must not leave raw
    ``bytes`` in the payload.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _tool_result(payload, *, is_error: bool = False) -> dict:
    payload = _json_safe(payload)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    out = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if isinstance(payload, dict):
        out["structuredContent"] = payload
    return out


def _origin_allowed(event: dict) -> bool:
    """Reject browser origins that were not explicitly registered.

    Server-to-server MCP clients commonly omit Origin. When a browser runtime
    supplies one, Streamable HTTP requires the server to validate it to avoid
    DNS-rebinding attacks.
    """
    headers = event.get("headers") or {}
    origin = next(
        (value for key, value in headers.items() if key.lower() == "origin"),
        None,
    )
    return origin is None or origin in ALLOWED_ORIGINS


def lambda_handler(event, _context):
    if not _origin_allowed(event):
        return _response(403, {"error": "Forbidden origin"})

    method = event.get("requestContext", {}).get("http", {}).get("method")
    if method and method.upper() != "POST":
        response = _response(405, {"error": "Method not allowed"})
        response["headers"]["allow"] = "POST"
        return response

    raw_body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except (ValueError, UnicodeError):
            return _error(None, -32700, "Invalid request encoding", status=400)
    try:
        request = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError):
        return _error(None, -32700, "Invalid JSON", status=400)
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid JSON-RPC request", status=400)

    request_id = request.get("id")
    rpc_method = request.get("method")
    if not isinstance(rpc_method, str):
        return _error(
            request_id, -32600, "Invalid JSON-RPC request", status=400
        )
    if rpc_method.startswith("notifications/"):
        return _response(202)
    if request_id is None:
        return _response(202)
    if rpc_method == "initialize":
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "params must be an object")
        requested_version = params.get("protocolVersion")
        version = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION
        )
        return _result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Read replica of the email-receipt SQLite primary. Money "
                    "is integer cents in raw columns and dollars in tool "
                    "output. Call replica_status to learn how stale the "
                    "data is. Writes are not available here."
                ),
            },
            protocol_version=version,
        )
    if rpc_method == "ping":
        return _result(request_id, {})
    if rpc_method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if rpc_method in ("resources/list", "resources/templates/list"):
        key = "resourceTemplates" if "templates" in rpc_method else "resources"
        return _result(request_id, {key: []})
    if rpc_method == "prompts/list":
        return _result(request_id, {"prompts": []})
    if rpc_method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name") if isinstance(params, dict) else None
        if name not in TOOL_NAMES:
            return _error(request_id, -32602, "Unknown tool")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _result(
                request_id,
                _tool_result(
                    {"error": "arguments must be an object"}, is_error=True
                ),
            )
        try:
            payload = _call_tool(name, arguments)
        except KeyError as exc:
            payload = {"error": f"missing argument: {exc}"}
            return _result(request_id, _tool_result(payload, is_error=True))
        except (ValueError, TypeError) as exc:
            payload = {"error": f"bad argument: {exc}"}
            return _result(request_id, _tool_result(payload, is_error=True))
        except sqlite3.Error as exc:
            payload = {"error": f"sqlite error: {exc}"}
            return _result(request_id, _tool_result(payload, is_error=True))
        except (ReplicaMissing, s3.exceptions.NoSuchKey):
            payload = {
                "error": (
                    f"replica not published yet: s3://{BUCKET}/{DB_KEY} is "
                    "missing. Run `emlrec replicate` on the primary."
                )
            }
            return _result(request_id, _tool_result(payload, is_error=True))
        is_error = isinstance(payload, dict) and "error" in payload
        return _result(request_id, _tool_result(payload, is_error=is_error))
    return _error(request_id, -32601, "Method not found")
