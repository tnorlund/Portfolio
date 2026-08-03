#!/usr/bin/env python3
"""DEV-ONLY data shim behind /dev/validation and /dev/geometric-reader.

Serves the local review workstation from the dev DynamoDB table. Nothing
here ships: next.config.js only wires these routes in
PHASE_DEVELOPMENT_SERVER, and no Lambda imports this module.

The reconciliation math is NOT reimplemented here. Per-receipt diagnostics
come from ``get_receipt_line_items_impl`` and the baseline rule from
``_summary_baseline``, both loaded out of scripts/receipt_mcp_server.py so
the harness and the agent-facing MCP tools can never disagree. Geometry and
image payloads reuse the deployed line_item_decode handler's helpers.

Run:
    python portfolio/dev-harness/validation_shim.py [--port 8787]

Agent work reaches the reviewer as files, never as rows: an ordered queue in
``.dev-harness/queues/<name>.json`` and a per-receipt dossier in
``.dev-harness/dossiers/<image_id>-<receipt_id>.json``. Both are read-only
here; the review log is the only thing this process writes.

Environment:
    DYNAMODB_TABLE_NAME    defaults to the dev table, ReceiptsTable-dc5be22
    VALIDATION_HARNESS_DIR defaults to <repo>/.dev-harness
    VALIDATION_REVIEW_LOG  defaults to <harness dir>/review_log.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TABLE = "ReceiptsTable-dc5be22"
DEFAULT_PORT = 8787
# The full index is two type-GSI scans (~2s on dev); cache so that clicking
# through a merchant's receipts doesn't rescan on every request.
INDEX_TTL_SECONDS = 90

os.environ.setdefault("DYNAMODB_TABLE_NAME", DEFAULT_TABLE)
TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def _load_module(name: str, relative_path: str):
    """Import a repo file that isn't part of an installed package."""
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mcp_server = _load_module("_validation_mcp", "scripts/receipt_mcp_server.py")
decode_route = _load_module(
    "_validation_decode_route",
    "infra/routes/line_item_decode/handler/index.py",
)

from receipt_dynamo import DynamoClient  # noqa: E402  (after path setup)
from receipt_dynamo.data.shared_exceptions import (  # noqa: E402
    EntityNotFoundError,
)
from receipt_upload.line_items.geometry import reconcile  # noqa: E402

HARNESS_DIR = Path(
    os.environ.get("VALIDATION_HARNESS_DIR", str(REPO_ROOT / ".dev-harness"))
)
DOSSIER_DIR = HARNESS_DIR / "dossiers"
QUEUE_DIR = HARNESS_DIR / "queues"
REVIEW_LOG = Path(
    os.environ.get(
        "VALIDATION_REVIEW_LOG", str(HARNESS_DIR / "review_log.jsonl")
    )
)

# confirm/flag are the reviewer's eyes; approve-fix queues the post-session
# writer; golden promotes into the bank-proven fixture set.
REVIEW_VERDICTS = ("confirm", "flag", "approve-fix", "golden")

# Failures first: the point of the harness is to look at what's broken.
STATUS_ORDER = {"mismatch": 0, "near": 1, "no-baseline": 2, "match": 3}
_SEVERITY = mcp_server._RECON_SEVERITY

_client = DynamoClient(TABLE_NAME)
_index_lock = threading.Lock()
_index_cache: dict[str, Any] = {"built_at": 0.0, "data": None}
_review_lock = threading.Lock()


def _run(coro):
    """The MCP impls are async; each request gets its own loop."""
    return asyncio.run(coro)


def _read_json(path: Path) -> tuple[Any, Optional[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name}: {type(exc).__name__}: {exc}"


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _dry_run_payload(value: Any) -> Optional[dict[str, Any]]:
    """The before/after pair the reviewer approves against.

    A proposal whose dry run is missing is still shown, but the UI has to be
    able to say so — hence None rather than zero-filled deltas.
    """
    if not isinstance(value, dict):
        return None
    return {
        "before_delta": _optional_float(value.get("before_delta")),
        "after_delta": _optional_float(value.get("after_delta")),
        "before_status": _optional_str(value.get("before_status")),
        "after_status": _optional_str(value.get("after_status")),
    }


def _proposal_payload(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    tool = _optional_str(value.get("tool"))
    if not tool:
        return None
    args = value.get("args")
    return {
        "tool": tool,
        "args": args if isinstance(args, dict) else {},
        "dry_run": _dry_run_payload(value.get("dry_run")),
    }


def _normalize_dossier(payload: dict, source: str) -> dict[str, Any]:
    """Give the UI a stable shape whatever the scout agent wrote."""
    evidence = payload.get("evidence")
    return {
        "failure_mode": _optional_str(payload.get("failure_mode")),
        "diagnosis": _optional_str(payload.get("diagnosis")) or "",
        "evidence": evidence if isinstance(evidence, list) else [],
        "proposal": _proposal_payload(payload.get("proposal")),
        "abstain_reason": _optional_str(payload.get("abstain_reason")),
        "generated_at": _optional_str(payload.get("generated_at")),
        "author": _optional_str(payload.get("author")),
        "source": source,
    }


def _read_dossier(
    image_id: str, receipt_id: int
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    path = DOSSIER_DIR / f"{image_id}-{receipt_id}.json"
    if not path.is_file():
        return None, None
    payload, error = _read_json(path)
    if error is not None:
        return None, error
    if not isinstance(payload, dict):
        return None, f"{path.name}: expected a JSON object"
    return _normalize_dossier(payload, path.name), None


def _queue_receipts(payload: Any) -> list[dict[str, Any]]:
    """Accept a bare ordered list or {"receipts": [...]}, ids only."""
    rows = payload.get("receipts") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        image_id = _optional_str(row.get("image_id"))
        try:
            receipt_id = int(row.get("receipt_id"))
        except (TypeError, ValueError):
            continue
        if image_id:
            entries.append({"image_id": image_id, "receipt_id": receipt_id})
    return entries


def _queue_path(name: str) -> Optional[Path]:
    """Reject anything that could climb out of the queue directory."""
    cleaned = name.strip()
    if not cleaned or cleaned != Path(cleaned).name or cleaned.startswith("."):
        return None
    if not cleaned.endswith(".json"):
        cleaned = f"{cleaned}.json"
    return QUEUE_DIR / cleaned


def handle_queues(_params: dict) -> dict[str, Any]:
    queues = []
    for path in sorted(QUEUE_DIR.glob("*.json")) if QUEUE_DIR.is_dir() else []:
        payload, error = _read_json(path)
        entry: dict[str, Any] = {
            "name": path.stem,
            "count": 0,
            "description": None,
            "error": error,
        }
        if error is None:
            entry["count"] = len(_queue_receipts(payload))
            if isinstance(payload, dict):
                entry["description"] = _optional_str(
                    payload.get("description")
                )
        queues.append(entry)
    return {"queues": queues, "dir": str(QUEUE_DIR)}


def _receipt_status(statuses: set) -> str:
    """Worst item status wins, matching list_reconciliation_worklist_impl."""
    if not statuses:
        return "no-baseline"
    return max(statuses, key=lambda s: _SEVERITY.get(s, -1))


def _build_index() -> dict[str, Any]:
    """Index receipt headers, line items, and summaries into review rows.

    Starting from receipt headers is intentional: missing line items are a
    validation target, not a reason for the receipt to disappear from the UI.
    """
    buckets: dict[tuple, dict[str, Any]] = {}
    last_key = None
    while True:
        batch, last_key = _client.list_receipt_line_items(
            limit=1000, last_evaluated_key=last_key
        )
        for li in batch:
            bucket = buckets.setdefault(
                (li.image_id, li.receipt_id),
                {
                    "merchant": None,
                    "items": 0,
                    "items_sum": 0.0,
                    "statuses": set(),
                },
            )
            bucket["items"] += 1
            if not li.is_discount:
                bucket["items_sum"] += float(li.price)
            if li.merchant_name and not bucket["merchant"]:
                bucket["merchant"] = li.merchant_name
            if li.reconciliation_status:
                bucket["statuses"].add(li.reconciliation_status)
        if last_key is None:
            break

    # Seed every receipt before joining the derived records. A receipt with no
    # sections, summary, or line items must still be reviewable.
    last_key = None
    while True:
        batch, last_key = _client.list_receipts(
            limit=1000, last_evaluated_key=last_key
        )
        for receipt in batch:
            buckets.setdefault(
                (receipt.image_id, receipt.receipt_id),
                {
                    "merchant": None,
                    "items": 0,
                    "items_sum": 0.0,
                    "statuses": set(),
                },
            )
        if last_key is None:
            break

    summaries: dict[tuple, Any] = {}
    last_key = None
    while True:
        batch, last_key = _client.list_receipt_summaries(
            limit=1000, last_evaluated_key=last_key
        )
        for record in batch:
            key = (record.image_id, record.receipt_id)
            summaries[key] = record
            bucket = buckets.setdefault(
                key,
                {
                    "merchant": None,
                    "items": 0,
                    "items_sum": 0.0,
                    "statuses": set(),
                },
            )
            if record.merchant_name and not bucket["merchant"]:
                bucket["merchant"] = record.merchant_name
        if last_key is None:
            break

    rows = []
    for (image_id, receipt_id), bucket in buckets.items():
        record = summaries.get((image_id, receipt_id))
        baseline = None
        figures: dict[str, Any] = {}
        if record is not None:
            figures, baseline = mcp_server._summary_baseline(record)
        items_sum = round(bucket["items_sum"], 2)
        receipt_status = _receipt_status(bucket["statuses"])
        if not bucket["statuses"]:
            # Reuse the production reconciliation ladder. In particular, a
            # receipt with a baseline but no extracted items is a mismatch.
            receipt_status, _, _ = reconcile(
                ([{"price": items_sum}] if bucket["items"] else []),
                figures if record is not None else None,
            )
        rows.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": (
                    (record.merchant_name if record else None)
                    or bucket["merchant"]
                    or "Unknown"
                ),
                "status": receipt_status,
                "items": bucket["items"],
                "items_sum": items_sum,
                "baseline": baseline,
                "subtotal": figures.get("subtotal"),
                "grand_total": figures.get("grand_total"),
                "tax": figures.get("tax"),
                "delta": (
                    round(items_sum - baseline, 2)
                    if baseline is not None
                    else None
                ),
                "tender_class": record.tender_class if record else None,
                "card_network": record.card_network if record else None,
                "card_last4": record.card_last4 if record else None,
                "ledger": record.ledger if record else None,
                "bank_amount": record.bank_amount if record else None,
                "bank_match_confidence": (
                    record.bank_match_confidence if record else None
                ),
            }
        )

    merchants: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = merchants.setdefault(
            row["merchant"],
            {
                "name": row["merchant"],
                "receipts": 0,
                "match": 0,
                "near": 0,
                "mismatch": 0,
                "no-baseline": 0,
                "with_bank": 0,
            },
        )
        entry["receipts"] += 1
        entry[row["status"]] += 1
        if row["bank_amount"] is not None:
            entry["with_bank"] += 1
    for entry in merchants.values():
        entry["match_rate"] = round(entry["match"] / entry["receipts"], 4)

    merchant_list = sorted(
        merchants.values(),
        key=lambda m: (
            -(m["mismatch"] + m["near"]),
            -m["receipts"],
            m["name"],
        ),
    )
    return {
        "rows": rows,
        "merchants": merchant_list,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "table": TABLE_NAME,
    }


def _index(refresh: bool = False) -> dict[str, Any]:
    with _index_lock:
        fresh = (
            _index_cache["data"] is not None
            and time.time() - _index_cache["built_at"] < INDEX_TTL_SECONDS
        )
        if refresh or not fresh:
            _index_cache["data"] = _build_index()
            _index_cache["built_at"] = time.time()
        return _index_cache["data"]


def _sort_key(row: dict[str, Any]):
    """Failures first, then worst |delta| first inside each status."""
    delta = row.get("delta")
    return (
        STATUS_ORDER.get(row["status"], 9),
        0 if delta is not None else 1,
        -abs(delta) if delta is not None else 0.0,
        row["image_id"],
        row["receipt_id"],
    )


def handle_merchants(params: dict) -> dict[str, Any]:
    index = _index(refresh=params.get("refresh", ["0"])[0] == "1")
    totals = {status: 0 for status in STATUS_ORDER}
    for row in index["rows"]:
        totals[row["status"]] += 1
    return {
        "merchants": index["merchants"],
        "totals": totals,
        "receipts": len(index["rows"]),
        "built_at": index["built_at"],
        "table": index["table"],
    }


def _queue_worklist(index: dict[str, Any], name: str) -> dict[str, Any]:
    """A curated queue is an order, not a filter: keep the file's sequence."""
    path = _queue_path(name)
    if path is None:
        return {"error": f"invalid queue name {name!r}"}
    if not path.is_file():
        return {"error": f"no queue {name!r} in {QUEUE_DIR}"}
    payload, error = _read_json(path)
    if error is not None:
        return {"error": error}

    by_key = {
        (row["image_id"], row["receipt_id"]): row for row in index["rows"]
    }
    ordered, missing = [], []
    for entry in _queue_receipts(payload):
        row = by_key.get((entry["image_id"], entry["receipt_id"]))
        if row is None:
            missing.append(entry)
        else:
            ordered.append(row)
    return {
        "queue": path.stem,
        "queue_description": (
            _optional_str(payload.get("description"))
            if isinstance(payload, dict)
            else None
        ),
        "matching": len(ordered),
        "receipts": ordered,
        "missing": missing,
        "built_at": index["built_at"],
    }


def handle_worklist(params: dict) -> dict[str, Any]:
    index = _index(refresh=params.get("refresh", ["0"])[0] == "1")
    queue = (params.get("queue", [""])[0] or "").strip()
    if queue:
        return _queue_worklist(index, queue)
    merchant = (params.get("merchant", [""])[0] or "").strip().lower()
    status = (params.get("status", ["all"])[0] or "all").strip().lower()
    try:
        limit = max(1, min(int(params.get("limit", ["250"])[0]), 1000))
    except (TypeError, ValueError):
        limit = 250
    if status not in STATUS_ORDER and status not in ("all", "failures"):
        return {"error": f"invalid status {status!r}"}

    rows = index["rows"]
    if merchant:
        rows = [r for r in rows if merchant in (r["merchant"] or "").lower()]
    if status == "failures":
        rows = [r for r in rows if r["status"] in ("mismatch", "near")]
    elif status != "all":
        rows = [r for r in rows if r["status"] == status]

    ordered = sorted(rows, key=_sort_key)
    return {
        "merchant": params.get("merchant", [""])[0],
        "status": status,
        "queue": None,
        "matching": len(ordered),
        "receipts": ordered[:limit],
        "built_at": index["built_at"],
    }


def _summary_payload(image_id: str, receipt_id: int) -> Optional[dict]:
    """Summary figures plus the tender/bank truth added in PR #1322."""
    try:
        record = _client.get_receipt_summary(image_id, receipt_id)
    except EntityNotFoundError:
        return None
    figures, baseline = mcp_server._summary_baseline(record)
    return {
        **figures,
        "baseline": baseline,
        "merchant_name": record.merchant_name,
        "tender_class": record.tender_class,
        "card_network": record.card_network,
        "card_last4": record.card_last4,
        "ledger": record.ledger,
        "bank_amount": record.bank_amount,
        "bank_match_confidence": record.bank_match_confidence,
    }


def handle_receipt(params: dict) -> dict[str, Any]:
    image_id = (params.get("image_id", [""])[0] or "").strip()
    try:
        receipt_id = int(params.get("receipt_id", [""])[0])
    except (TypeError, ValueError):
        return {"error": "receipt_id must be an integer"}
    if not image_id:
        return {"error": "image_id is required"}

    diagnostic = _run(
        mcp_server.get_receipt_line_items_impl(_client, image_id, receipt_id)
    )
    if "error" in diagnostic:
        return diagnostic

    try:
        details = _client.get_receipt_details(image_id, receipt_id)
    except EntityNotFoundError:
        # Orphaned derived records are precisely the sort of incomplete data
        # this workstation exists to surface. Keep the diagnostics visible.
        details = None
    try:
        sections = _client.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )
    except EntityNotFoundError:
        sections = []

    summary = _summary_payload(image_id, receipt_id)
    dossier, dossier_error = _read_dossier(image_id, receipt_id)
    merchant = (summary or {}).get("merchant_name")
    if not merchant and details is not None and details.place is not None:
        merchant = details.place.merchant_name

    return {
        **diagnostic,
        "merchant_name": merchant or "Unknown",
        "image": (
            decode_route._image_payload(details.receipt)
            if details is not None
            else None
        ),
        "lines": [
            decode_route._line_payload(line)
            for line in sorted(
                details.lines if details is not None else [],
                key=lambda value: value.line_id,
            )
        ],
        "sections": [
            {
                "section_type": str(section.section_type),
                "line_ids": sorted(set(section.line_ids)),
                "validation_status": section.validation_status or "NONE",
            }
            for section in sections
        ],
        "summary": summary,
        "dossier": dossier,
        "dossier_error": dossier_error,
        "reviews": _read_reviews(image_id, receipt_id),
    }


def _read_reviews(
    image_id: Optional[str] = None, receipt_id: Optional[int] = None
) -> list:
    if not REVIEW_LOG.exists():
        return []
    entries = []
    with REVIEW_LOG.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if image_id is not None and entry.get("image_id") != image_id:
                continue
            if (
                receipt_id is not None
                and entry.get("receipt_id") != receipt_id
            ):
                continue
            entries.append(entry)
    return entries


def _coerce_line_ids(value: Any) -> list[int]:
    """Which rows the verdict is about — free text can't point at a row."""
    if not isinstance(value, list):
        return []
    line_ids = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            line_ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(line_ids)


def handle_review_post(body: dict) -> dict[str, Any]:
    verdict = str(body.get("verdict", "")).strip().lower()
    if verdict not in REVIEW_VERDICTS:
        return {
            "error": f"verdict must be one of {', '.join(REVIEW_VERDICTS)}"
        }
    image_id = str(body.get("image_id", "")).strip()
    if not image_id:
        return {"error": "image_id is required"}
    try:
        receipt_id = int(body.get("receipt_id"))
    except (TypeError, ValueError):
        return {"error": "receipt_id must be an integer"}

    entry = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "verdict": verdict,
        "note": str(body.get("note", "") or ""),
        # The A-J failure-mode letter (or a hint code) the reviewer agreed
        # with, so a verdict can be joined back to the taxonomy.
        "reason": _optional_str(body.get("reason")),
        "line_ids": _coerce_line_ids(body.get("line_ids")),
        "merchant": str(body.get("merchant", "") or ""),
        "status": str(body.get("status", "") or ""),
        "delta": body.get("delta"),
        "author": str(body.get("author", "user") or "user"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with _review_lock:
        REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REVIEW_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    return {"ok": True, "entry": entry, "log": str(REVIEW_LOG)}


class ValidationHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802  (BaseHTTPRequestHandler API)
        self._send(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        try:
            if route == "/line_item_decode":
                # Unchanged contract for /dev/geometric-reader.
                event = {
                    "requestContext": {"http": {"method": "GET"}},
                    "queryStringParameters": {
                        key: values[0] for key, values in params.items()
                    }
                    or {"batch_size": "10"},
                }
                response = decode_route.handler(event, None)
                self._send(
                    response.get("statusCode", 200),
                    json.loads(response["body"]),
                )
                return
            if route == "/merchants":
                self._send(200, handle_merchants(params))
                return
            if route == "/queues":
                self._send(200, handle_queues(params))
                return
            if route == "/worklist":
                payload = handle_worklist(params)
                self._send(400 if "error" in payload else 200, payload)
                return
            if route == "/receipt":
                payload = handle_receipt(params)
                self._send(400 if "error" in payload else 200, payload)
                return
            if route == "/review":
                self._send(
                    200,
                    {"entries": _read_reviews(), "log": str(REVIEW_LOG)},
                )
                return
            if route in ("/", "/health"):
                self._send(
                    200,
                    {
                        "ok": True,
                        "table": TABLE_NAME,
                        "review_log": str(REVIEW_LOG),
                        "dossiers": str(DOSSIER_DIR),
                        "queues": str(QUEUE_DIR),
                        "verdicts": list(REVIEW_VERDICTS),
                        "routes": [
                            "/merchants",
                            "/queues",
                            "/worklist",
                            "/receipt",
                            "/review",
                            "/line_item_decode",
                        ],
                    },
                )
                return
            self._send(404, {"error": f"no route {route}"})
        except Exception as exc:  # dev tool: surface the failure in the UI
            import traceback

            traceback.print_exc()
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "body must be JSON"})
            return
        if route != "/review":
            self._send(404, {"error": f"no route {route}"})
            return
        payload = handle_review_post(body)
        self._send(400 if "error" in payload else 200, payload)

    def log_message(self, *args) -> None:  # quiet; the page is the UI
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(
        f"validation shim: http://{args.host}:{args.port} "
        f"table={TABLE_NAME} review_log={REVIEW_LOG} "
        f"harness_dir={HARNESS_DIR}",
        flush=True,
    )
    ThreadingHTTPServer(
        (args.host, args.port), ValidationHandler
    ).serve_forever()


if __name__ == "__main__":
    main()
