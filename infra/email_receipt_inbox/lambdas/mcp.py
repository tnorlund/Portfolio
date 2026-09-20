"""MCP server over the agent-safe spend projection.

The SQLite file on the Mac (``~/receipts-email/email_receipts.db``) is the
primary and is never served from here. ``emlrec publish-projection`` exports
exactly two tables, ``spend`` and ``txn``, into a fresh database, validates
them against an explicit column allowlist, and uploads the gzipped file plus
a manifest to ``s3://<mail bucket>/agent/``. This Lambda's role can read
that prefix and nothing else. On every download the file is validated again
against the same contract (exact tables, exact columns, no views or
triggers, matching schema version); a file that fails is never served and
there is no fallback to any other database.

Two tools: ``query_sql`` (read-only SELECT/WITH over ``spend`` and ``txn``
under a SQLite authorizer, with a statement time budget, a row cap, and a
response-size cap) and ``replica_status`` (freshness and the schema).

Transport: stateless MCP Streamable HTTP behind the shared Cognito gateway
(``/email/mcp``). No dependencies beyond the Lambda runtime (boto3, sqlite3).
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ["PROJECTION_BUCKET"]
DB_KEY = os.environ.get("PROJECTION_DB_KEY", "agent/spend.db.gz")
MANIFEST_KEY = os.environ.get("PROJECTION_MANIFEST_KEY", "agent/manifest.json")
CACHE_DIR = os.environ.get("PROJECTION_CACHE_DIR", "/tmp/email-projection")
# How long a warm container trusts its cached ETags before HEADing S3 again.
ETAG_CHECK_SECONDS = int(os.environ.get("PROJECTION_ETAG_CHECK_SECONDS", "60"))
# Budget for a single query_sql statement; the gateway integration window is
# 29s and the function timeout is 25s, so abort well before either.
SQL_BUDGET_SECONDS = float(
    os.environ.get("PROJECTION_SQL_BUDGET_SECONDS", "10")
)
# Row cap (default and hard ceiling) and response-size cap for query_sql.
DEFAULT_LIMIT = 500
MAX_LIMIT = int(os.environ.get("PROJECTION_MAX_LIMIT", "1000"))
MAX_RESPONSE_BYTES = int(
    os.environ.get("PROJECTION_MAX_RESPONSE_BYTES", "262144")
)
ALLOWED_ORIGINS = {
    value.strip()
    for value in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if value.strip()
}

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, "2024-11-05", "2025-03-26"}
SERVER_INFO = {"name": "portfolio-email-receipts", "version": "2.0.0"}

# ---------------------------------------------------------------------------
# The consumer side of the projection contract. The producer is
# receipts-email/emlrec/projection.py (SCHEMA_VERSION, COLUMNS,
# FORBIDDEN_COLUMNS); the two must agree exactly, and
# infra/tests/test_email_receipt_mcp.py runs the real exporter against this
# module when that checkout is available.
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 2
COLUMNS = {
    "spend": (
        "source",
        "date",
        "merchant_name",
        "merchant_category",
        "item_description",
        "quantity",
        "unit_price_cents",
        "total_cents",
        "receipt_total_cents",
        "receipt_ref",
        "currency",
    ),
    "txn": (
        "txn_date",
        "posting_date",
        "merchant_canonical",
        "category",
        "amount_cents",
        "txn_class",
        "is_card_purchase",
        "currency",
    ),
}
FORBIDDEN_COLUMNS = frozenset(
    {
        "message_id",
        "from_addr",
        "from_domain",
        "subject",
        "card_last4",
        "last4_kind",
        "account",
        "description",
        "order_id",
        "mbox_file",
        "byte_offset",
        "byte_length",
        "dedupe_key",
        "extra",
        "content_hash",
    }
)
GRAIN_NOTE = (
    "spend is one row per receipt ITEM: receipt_total_cents repeats on every "
    "item of a receipt (sum total_cents, or SELECT DISTINCT receipt_ref, "
    "receipt_total_cents for receipt totals); email and paper sources are "
    "not deduplicated against each other; money is integer cents and every "
    "row carries its currency (NULL = unknown, never assume USD), so "
    "aggregate per currency."
)

s3 = boto3.client("s3")

_state: dict = {
    "etag": None,
    "conn": None,
    "checked_at": 0.0,
    "manifest": None,
    "manifest_etag": None,
    "loaded_at": None,
}


class ProjectionMissing(Exception):
    """The projection has never been published (or was deleted)."""


class ProjectionInvalid(Exception):
    """The downloaded file does not satisfy the projection contract."""


# ---------------------------------------------------------------------------
# Snapshot lifecycle
# ---------------------------------------------------------------------------
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

    The publisher uploads the database and the manifest as two PUTs, so a
    request between them must not pin a stale manifest to the new database
    for the rest of the check interval.
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


def validate_projection(conn: sqlite3.Connection) -> None:
    """Enforce the producer contract on a downloaded file, or refuse it.

    Exactly the ``spend`` and ``txn`` tables, no views or triggers, exactly
    the allow-listed columns in order (``table_xinfo`` includes hidden and
    generated columns), none of the forbidden column names, and the
    supported ``user_version``. Raises :class:`ProjectionInvalid`.
    """
    objects = conn.execute(
        "SELECT name, type FROM sqlite_schema "
        "WHERE type IN ('table', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {name for name, kind in objects if kind == "table"}
    if tables != set(COLUMNS):
        raise ProjectionInvalid(
            "projection must contain exactly the spend and txn tables; "
            f"found {sorted(tables)}"
        )
    extras = sorted(
        f"{kind} {name}" for name, kind in objects if kind != "table"
    )
    if extras:
        raise ProjectionInvalid(
            f"projection must not contain views or triggers: {extras}"
        )
    for table, expected in COLUMNS.items():
        columns = tuple(
            row[1] for row in conn.execute(f"PRAGMA table_xinfo({table})")
        )
        forbidden = FORBIDDEN_COLUMNS.intersection(c.lower() for c in columns)
        if forbidden:
            raise ProjectionInvalid(
                f"forbidden column in {table}: {', '.join(sorted(forbidden))}"
            )
        if columns != expected:
            raise ProjectionInvalid(
                f"unexpected columns in {table}: {list(columns)}"
            )
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise ProjectionInvalid(
            f"projection schema version {version} is not the supported "
            f"version {SCHEMA_VERSION}"
        )


def _open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    try:
        validate_projection(conn)
    except ProjectionInvalid:
        conn.close()
        raise
    return conn


def _connection() -> sqlite3.Connection:
    """Return a read-only connection to the freshest validated projection."""
    now = time.monotonic()
    if (
        _state["conn"] is not None
        and now - _state["checked_at"] < ETAG_CHECK_SECONDS
    ):
        return _state["conn"]
    head = _head(DB_KEY)
    if head is None:
        raise ProjectionMissing(DB_KEY)
    etag = head["ETag"].strip('"')
    _state["checked_at"] = now
    _refresh_manifest()
    if etag == _state["etag"] and _state["conn"] is not None:
        return _state["conn"]
    path = _download(etag)
    try:
        conn = _open(path)
    except ProjectionInvalid:
        # Never serve an invalid file, and never keep serving a previous one
        # as if it were current: drop it so every call reports the failure.
        previous = _state["conn"]
        _state.update(etag=None, conn=None, loaded_at=None)
        if previous is not None:
            previous.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
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
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in COLUMNS
    }
    currencies = {
        table: {
            (row[0] or "unknown"): row[1]
            for row in conn.execute(
                f"SELECT currency, COUNT(*) FROM {table} GROUP BY 1 ORDER BY 1"
            )
        }
        for table in COLUMNS
    }
    return {
        "role": "agent-safe projection (read-only)",
        "primary": "~/receipts-email/email_receipts.db on the Mac; only the "
        "spend/txn projection is published here",
        "bucket": BUCKET,
        "key": DB_KEY,
        "etag": _state["etag"],
        "loaded_at": _state["loaded_at"],
        "manifest": manifest,
        "replica_age_seconds": age_seconds,
        "schema_version": SCHEMA_VERSION,
        "tables": {table: list(cols) for table, cols in COLUMNS.items()},
        "row_counts": counts,
        "currencies": currencies,
        "grain": GRAIN_NOTE,
        "writes": "not available here; ingest, reconciliation, and match "
        "decisions run on the primary and land with the next publish",
    }


# ---------------------------------------------------------------------------
# query_sql: SELECT/WITH over spend and txn only
# ---------------------------------------------------------------------------
_SQL_READ_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}
# String literals, quoted identifiers, and comments: a denied verb inside
# `LIKE '%update%'` or `-- drop this` is data, not a statement.
_SQL_LITERALS = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]|--[^\n]*|/\*.*?\*/",
    re.S,
)
_SQL_DENY = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|"
    r"replace|reindex|analyze|begin|commit|rollback|savepoint|release)\b",
    re.I,
)


def _sql_authorizer(action, table, _column, _db, _trigger):
    """Reads of ``spend`` and ``txn`` only; everything else is denied.

    Writes, PRAGMA, ATTACH, transactions, and reads of any other object
    (including ``sqlite_master``) fail at prepare time with "not
    authorized", independent of the keyword denylist.
    """
    if action in _SQL_READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and table in COLUMNS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _json_safe(value):
    """Coerce SQLite values into JSON: BLOBs become base64 strings.

    The projection has no BLOB columns, but a literal such as ``x'00'`` can
    still produce ``bytes``; ``structuredContent`` is serialized by the
    response-level ``json.dumps`` which has no ``default=`` hook.
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


def query_sql(conn: sqlite3.Connection, sql: str, limit: int) -> dict:
    bare = _SQL_LITERALS.sub(" ", sql)
    if _SQL_DENY.search(bare) or not re.match(
        r"\s*(select|with)\b", bare, re.I
    ):
        return {"error": "read-only: only SELECT/WITH queries are allowed"}
    deadline = time.monotonic() + SQL_BUDGET_SECONDS

    def _check():
        if time.monotonic() > deadline:
            return 1  # non-zero aborts the statement
        return 0

    conn.set_progress_handler(_check, 10_000)
    conn.set_authorizer(_sql_authorizer)
    try:
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(limit)
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
                    f"{text}. query_sql is read-only over the projection "
                    "tables spend and txn; nothing else is readable here"
                )
            }
        return {"error": f"sqlite error: {text}"}
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    out_rows = [_json_safe(list(r)) for r in rows]
    # Response-size cap: shed rows from the end until the payload fits.
    truncated = len(rows) == limit
    while out_rows and len(json.dumps(out_rows)) > MAX_RESPONSE_BYTES:
        out_rows = out_rows[: max(1, len(out_rows) // 2)]
        truncated = True
        if (
            len(out_rows) == 1
            and len(json.dumps(out_rows)) > MAX_RESPONSE_BYTES
        ):
            return {
                "error": (
                    f"a single row exceeds the {MAX_RESPONSE_BYTES}-byte "
                    "response cap; select fewer or narrower columns"
                )
            }
    return {
        "columns": cols,
        "row_count": len(out_rows),
        "rows": out_rows,
        "truncated": truncated,
    }


def _bounded(a: dict, key: str, default: int, *, minimum: int = 1) -> int:
    """Integer argument clamped to ``[minimum, MAX_LIMIT]``.

    SQLite reads ``LIMIT -1`` as "no limit", so negatives are rejected
    rather than clamped; oversize values are clamped so a single call can
    never serialize the whole projection.
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
# Tool surface
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "query_sql",
        "description": (
            "Read-only SQL (SELECT/WITH only, "
            f"{DEFAULT_LIMIT}-row default cap, {SQL_BUDGET_SECONDS:.0f}s "
            "budget) over the agent-safe spend projection. Exactly two "
            "tables. spend(" + ", ".join(COLUMNS["spend"]) + "): one row "
            "per email or paper receipt item. txn("
            + ", ".join(COLUMNS["txn"])
            + "): every card transaction, signed integer cents (negative = "
            "charge), merchant/category NULL when unmapped. "
            + GRAIN_NOTE
            + " No other table exists here: no message index, no receipt "
            "identifiers, no card numbers, no raw descriptors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "default": DEFAULT_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "replica_status",
        "description": (
            "How fresh the projection is: manifest (published_at, sha256, "
            "row counts, currencies), S3 ETag, age in seconds, the schema "
            "the two tables carry, and which operations are only available "
            "on the primary."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _call_tool(name: str, a: dict) -> dict:
    conn = _connection()
    if name == "query_sql":
        return query_sql(
            conn, str(a["sql"]), _bounded(a, "limit", DEFAULT_LIMIT)
        )
    if name == "replica_status":
        return _replica_status(conn)
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
                    "Read-only agent-safe projection of the email-receipt "
                    "primary: two tables, spend (receipt items) and txn "
                    "(card transactions), integer cents with a currency on "
                    "every row. Call replica_status for freshness and the "
                    "schema; use query_sql for everything else. Writes are "
                    "not available here."
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
        except (ProjectionMissing, s3.exceptions.NoSuchKey):
            payload = {
                "error": (
                    f"projection not published yet: s3://{BUCKET}/{DB_KEY} "
                    "is missing. Run `emlrec publish-projection` on the "
                    "primary."
                )
            }
            return _result(request_id, _tool_result(payload, is_error=True))
        except ProjectionInvalid as exc:
            payload = {
                "error": (
                    f"published projection rejected: {exc}. Nothing is "
                    "served until `emlrec publish-projection` uploads a "
                    "file that satisfies the contract."
                )
            }
            return _result(request_id, _tool_result(payload, is_error=True))
        is_error = isinstance(payload, dict) and "error" in payload
        return _result(request_id, _tool_result(payload, is_error=is_error))
    return _error(request_id, -32601, "Method not found")
