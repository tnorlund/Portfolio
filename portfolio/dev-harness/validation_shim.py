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

Agent work reaches the reviewer as files, never as rows:

    queues/<name>.json                 ordered escalation queue (T2)
    dossiers/<image_id>-<receipt_id>.json   per-receipt scout analysis
    verdicts/<pass-id>.jsonl           adjudicated verdicts, one per line
    verdicts/<pass-id>/digest.json     optional pre-grouped batch digest

Those four are read-only here. This process writes exactly three things:
the review log, ``approvals/<pass-id>.json`` (which T1 groups the human
signed off), and ``freeze/<class>`` markers (a failed blind audit, which
the adjudicator and writer must respect before touching that class again).

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
import math
import os
import random
import re
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
VERDICT_DIR = HARNESS_DIR / "verdicts"
APPROVAL_DIR = HARNESS_DIR / "approvals"
FREEZE_DIR = HARNESS_DIR / "freeze"
REVIEW_LOG = Path(
    os.environ.get(
        "VALIDATION_REVIEW_LOG", str(HARNESS_DIR / "review_log.jsonl")
    )
)

# confirm/flag are the reviewer's eyes; approve-fix queues the post-session
# writer; golden promotes into the bank-proven fixture set. The two audit
# verdicts come from the blind deck and are the only ones that can freeze a
# tier, so they are kept distinct from a plain confirm/flag.
AUDIT_VERDICTS = ("audit-agree", "audit-disagree")
REVIEW_VERDICTS = (
    "confirm",
    "flag",
    "approve-fix",
    "golden",
    *AUDIT_VERDICTS,
)

# Blind audit share of a pass's auto-applied verdicts, with a floor so a
# small pass is still sampled at all.
AUDIT_FRACTION = 0.10
AUDIT_MIN_SAMPLE = 3

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
    """Give the UI a stable shape across dossier v1 and v2.

    v2 renamed ``failure_mode`` to ``mode``, split the narrative into
    ``visual_evidence`` (transcription first, per the runbook's
    independence rule), and dropped the tool name from the proposal.
    """
    evidence = payload.get("evidence")
    visual = payload.get("visual_evidence")
    rows = (evidence if isinstance(evidence, list) else []) + (
        visual if isinstance(visual, list) else []
    )
    proposal = payload.get("proposal")
    return {
        "failure_mode": _optional_str(
            _first(payload, "mode", "failure_mode")
        ),
        "diagnosis": _optional_str(
            _first(payload, "diagnosis", "confidence_justification")
        )
        or "",
        "evidence": rows,
        "proposal": _v2_proposal(proposal) or _proposal_payload(proposal),
        "abstain_reason": _optional_str(payload.get("abstain_reason")),
        "verdict_recommendation": _optional_str(
            payload.get("verdict_recommendation")
        ),
        "confidence": _optional_str(payload.get("confidence")),
        "signals_concurring": [
            str(s)
            for s in (
                payload.get("signals_concurring")
                if isinstance(payload.get("signals_concurring"), list)
                else []
            )
        ],
        "generated_at": _optional_str(payload.get("generated_at")),
        "author": _optional_str(
            _first(payload, "author", "verdict_by", "generated_by")
        ),
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


# --------------------------------------------------------------------------
# Adjudicated verdicts: passes, digest groups, approvals, blind audit, freeze
# --------------------------------------------------------------------------

# The adjudicator names its tiers; the harness only cares about the three
# routes, so every spelling seen in the design docs maps onto one of them.
_TIER_ALIASES = {
    "t0": "T0",
    "auto": "T0",
    "auto-apply": "T0",
    "auto_apply": "T0",
    "apply": "T0",
    "t1": "T1",
    "digest": "T1",
    "batch": "T1",
    "batch-digest": "T1",
    "batch_digest": "T1",
    "t2": "T2",
    "escalate": "T2",
    "escalation": "T2",
    "abstain": "abstain",
    "abstained": "abstain",
}

_CLASS_LETTER = re.compile(r"^[A-Z]$")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _first(payload: dict, *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _freeze_class(value: Any) -> str:
    """The A-J class letter, matching ``agentic_adjudicate.mode_class``.

    The adjudicator matches freeze markers by tier name or by the mode's
    letter prefix, so the marker must be named the same way or the freeze
    is silently ignored on the next pass.
    """
    letter = (_optional_str(value) or "").split("-", 1)[0].upper()
    return letter if _CLASS_LETTER.match(letter) else "unclassified"


def _v2_proposal(value: Any) -> Optional[dict[str, Any]]:
    """Dossier-v2 proposal: line ids and a guard verdict, never a tool name.

    The tool is not in the file because there is only one write path; the
    label here has to match what agentic_writer.py actually runs.
    """
    if not isinstance(value, dict):
        return None
    add = value.get("add_line_ids")
    line_ids = _coerce_line_ids(add) if isinstance(add, list) else []
    before = value.get("before") if isinstance(value.get("before"), dict) else {}
    after = value.get("after") if isinstance(value.get("after"), dict) else {}
    return {
        "tool": "extend_items_section" if line_ids else None,
        "args": {"line_ids": line_ids},
        "verified": _as_bool(value.get("verified")),
        "contiguous": _as_bool(value.get("contiguous")),
        "vision_products_confirmed": _as_bool(
            value.get("vision_products_confirmed")
        ),
        "dry_run": {
            "before_delta": _optional_float(before.get("delta")),
            "after_delta": _optional_float(after.get("delta")),
            "before_status": _optional_str(before.get("status")),
            "after_status": _optional_str(after.get("status")),
        },
    }


def _normalize_verdict(payload: Any) -> Optional[dict[str, Any]]:
    """One adjudicated row from ``verdicts/<pass-id>.jsonl``."""
    if not isinstance(payload, dict):
        return None
    image_id = _optional_str(_first(payload, "image_id", "imageId"))
    try:
        receipt_id = int(_first(payload, "receipt_id", "receiptId"))
    except (TypeError, ValueError):
        return None
    if not image_id:
        return None

    tier_raw = _optional_str(_first(payload, "tier", "route", "tier_name"))
    tier = _TIER_ALIASES.get((tier_raw or "").lower(), tier_raw or "abstain")
    mode = _optional_str(_first(payload, "mode", "failure_mode", "class"))
    proposal = _v2_proposal(payload.get("proposal")) or _proposal_payload(
        payload.get("proposal")
    )
    dry_run = (proposal or {}).get("dry_run") or {}
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant": _optional_str(
            _first(payload, "merchant", "merchant_name")
        )
        or "Unknown",
        "failure_mode": mode,
        "tier": tier,
        "action": _optional_str(_first(payload, "action", "proposed_action"))
        or (proposal or {}).get("tool"),
        "proposal": proposal,
        "golden_candidate": _as_bool(
            _first(payload, "golden", "golden_candidate", "is_golden")
        ),
        "group_id": _optional_str(_first(payload, "group_id", "groupId")),
        # The verdict file carries the gap through the proposal's dry run,
        # not as a bare field.
        "delta": (
            _optional_float(payload.get("delta"))
            if payload.get("delta") is not None
            else dry_run.get("before_delta")
        ),
        "after_delta": dry_run.get("after_delta"),
        "reason": _optional_str(payload.get("reason")),
        "confidence": _optional_str(payload.get("confidence")),
        "verdict_recommendation": _optional_str(
            _first(payload, "verdict_recommendation", "recommendation")
        ),
        "diagnosis": _optional_str(payload.get("diagnosis")),
        "abstain_reason": _optional_str(payload.get("abstain_reason")),
        "verdict_by": _optional_str(payload.get("verdict_by")),
    }


def _read_verdict_lines(path: Path) -> tuple[list[dict], Optional[str]]:
    """JSONL is the contract; a bad line is reported, never silently dropped."""
    entries, bad = [], 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                entry = _normalize_verdict(row)
                if entry is None:
                    bad += 1
                else:
                    entries.append(entry)
    except OSError as exc:
        return [], f"{path.name}: {type(exc).__name__}: {exc}"
    return entries, (f"{path.name}: {bad} unreadable line(s)" if bad else None)


def _list_passes() -> list[dict[str, Any]]:
    """Newest first. A pass is a ``<id>.jsonl`` file or a ``<id>/`` directory."""
    if not VERDICT_DIR.is_dir():
        return []
    passes: dict[str, dict[str, Any]] = {}
    for path in VERDICT_DIR.iterdir():
        if path.name.startswith("."):
            continue
        if path.is_file() and path.suffix == ".jsonl":
            entry = passes.setdefault(path.stem, {"pass_id": path.stem})
            entry["verdicts_path"] = path
            entry["mtime"] = max(entry.get("mtime", 0.0), path.stat().st_mtime)
        elif path.is_dir():
            entry = passes.setdefault(path.name, {"pass_id": path.name})
            digest = path / "digest.json"
            verdicts = path / "verdicts.jsonl"
            if digest.is_file():
                entry["digest_path"] = digest
            if verdicts.is_file():
                entry["verdicts_path"] = verdicts
            entry["mtime"] = max(entry.get("mtime", 0.0), path.stat().st_mtime)
    # A sibling digest for the flat layout: verdicts/<id>.digest.json
    for path in VERDICT_DIR.glob("*.digest.json"):
        stem = path.name[: -len(".digest.json")]
        passes.setdefault(stem, {"pass_id": stem, "mtime": path.stat().st_mtime})
        passes[stem]["digest_path"] = path
    return sorted(
        passes.values(), key=lambda p: (-p.get("mtime", 0.0), p["pass_id"])
    )


def _select_pass(pass_id: Optional[str]) -> Optional[dict[str, Any]]:
    passes = _list_passes()
    if not passes:
        return None
    if not pass_id:
        return passes[0]
    for entry in passes:
        if entry["pass_id"] == pass_id:
            return entry
    return None


def _pass_entries(record: dict[str, Any]) -> tuple[list[dict], Optional[str]]:
    path = record.get("verdicts_path")
    if path is None:
        return [], None
    return _read_verdict_lines(path)


def _frozen_classes() -> list[str]:
    if not FREEZE_DIR.is_dir():
        return []
    return sorted(p.name for p in FREEZE_DIR.iterdir() if p.is_file())


def _approvals_path(pass_id: str) -> Optional[Path]:
    cleaned = pass_id.strip()
    if not cleaned or cleaned != Path(cleaned).name or cleaned.startswith("."):
        return None
    return APPROVAL_DIR / f"{cleaned}.json"


def _read_approvals(pass_id: str) -> dict[str, Any]:
    """The file agentic_writer.py reads: ``approved_groups`` is the contract.

    ``approval_log`` is this harness's own provenance and is preserved but
    never required; ``t2_retirements`` may be hand-written and must survive
    an approval written here.
    """
    empty: dict[str, Any] = {
        "pass_id": pass_id,
        "approved_groups": [],
        "t2_retirements": [],
        "approval_log": [],
    }
    path = _approvals_path(pass_id)
    if path is None or not path.is_file():
        return empty
    payload, error = _read_json(path)
    if error is not None or not isinstance(payload, dict):
        return empty
    return {
        **empty,
        **payload,
        "approved_groups": [
            str(g) for g in (payload.get("approved_groups") or [])
        ],
    }


def _slug(value: Any, fallback: str) -> str:
    """Same slug the adjudicator's group_id uses; ids must match exactly."""
    text = (_optional_str(value) or "").lower() or fallback
    cleaned = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return cleaned or fallback


def _group_id(entry: dict[str, Any]) -> str:
    return entry["group_id"] or (
        f"{_slug(entry['merchant'], 'unknown-merchant')}"
        f"::{_slug(entry['failure_mode'], 'unknown-mode')}"
    )


def _receipt_ref(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_id": entry["image_id"],
        "receipt_id": entry["receipt_id"],
        "delta": entry["delta"],
        "merchant": entry["merchant"],
        "reason": entry.get("reason"),
        "golden": entry["golden_candidate"],
    }


def _derive_digest(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the batch tier by merchant × mode, the unit a human approves."""
    groups: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry["tier"] != "T1":
            continue
        key = _group_id(entry)
        group = groups.setdefault(
            key,
            {
                "group_id": key,
                "merchant": entry["merchant"],
                "failure_mode": entry["failure_mode"] or "unclassified",
                "action": None,
                "golden_candidate": False,
                "receipts": [],
                "thumbnails": [],
            },
        )
        group["golden_candidate"] = (
            group["golden_candidate"] or entry["golden_candidate"]
        )
        group["action"] = group["action"] or entry["action"]
        group["receipts"].append(_receipt_ref(entry))
    return list(groups.values())


def _normalize_group(
    payload: Any, by_key: dict[tuple, dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """A ``t1_groups`` row from the adjudicator's digest.json.

    The digest names membership but not the money; the per-receipt figures
    are joined back from the verdicts file so the reviewer sees the gap the
    group would close rather than an empty column.
    """
    if not isinstance(payload, dict):
        return None
    receipts, golden = [], False
    raw = _first(payload, "receipts", "members", "rows")
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        image_id = _optional_str(_first(row, "image_id", "imageId"))
        try:
            receipt_id = int(_first(row, "receipt_id", "receiptId"))
        except (TypeError, ValueError):
            continue
        if not image_id:
            continue
        entry = by_key.get((image_id, receipt_id))
        golden = golden or _as_bool(row.get("golden"))
        receipts.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "delta": (entry or {}).get("delta"),
                "merchant": _optional_str(
                    _first(row, "merchant", "merchant_name")
                )
                or (entry or {}).get("merchant"),
                "reason": _optional_str(row.get("reason"))
                or (entry or {}).get("reason"),
                "golden": _as_bool(row.get("golden")),
            }
        )
    merchant = _optional_str(_first(payload, "merchant", "merchant_name"))
    mode = _optional_str(_first(payload, "mode", "failure_mode", "class"))
    thumbs = _first(payload, "thumbnails", "samples", "sample_thumbnails")
    action = _optional_str(_first(payload, "action", "proposed_action", "tool"))
    if action is None:
        action = next(
            (
                (by_key.get((r["image_id"], r["receipt_id"])) or {}).get("action")
                for r in receipts
                if (by_key.get((r["image_id"], r["receipt_id"])) or {}).get(
                    "action"
                )
            ),
            None,
        )
    try:
        golden_count = int(payload.get("golden_count"))
    except (TypeError, ValueError):
        golden_count = 0
    return {
        "group_id": _optional_str(_first(payload, "group_id", "groupId"))
        or f"{_slug(merchant, 'unknown-merchant')}::{_slug(mode, 'unknown-mode')}",
        "merchant": merchant or "Unknown",
        "failure_mode": mode or "unclassified",
        "action": action,
        "golden_candidate": golden_count > 0
        or golden
        or _as_bool(_first(payload, "golden_candidate", "is_golden")),
        "receipts": receipts,
        # A digest that states its own count is trusted over the row list,
        # which may be truncated for display.
        "count": _first(payload, "count", "n", "receipt_count"),
        "thumbnails": [
            str(t) for t in (thumbs if isinstance(thumbs, list) else [])
        ],
    }


def _finalize_groups(
    groups: list[dict[str, Any]], approvals: dict[str, Any]
) -> list[dict[str, Any]]:
    approved = set(approvals.get("approved_groups") or [])
    frozen = set(_frozen_classes())
    rows = []
    for group in groups:
        try:
            count = int(_first(group, "count"))
        except (TypeError, ValueError):
            count = 0
        deltas = [
            r["delta"] for r in group["receipts"] if r["delta"] is not None
        ]
        rows.append(
            {
                **group,
                "count": count or len(group["receipts"]),
                "net_delta": round(sum(deltas), 2) if deltas else None,
                "approved": group["group_id"] in approved,
                # The adjudicator freezes by tier name or class letter; the
                # digest is entirely T1, so both markers apply here.
                "frozen": bool(
                    frozen & {_freeze_class(group["failure_mode"]), "T1"}
                ),
            }
        )
    # Golden candidates ratchet the CI floors, so they lead the digest.
    return sorted(
        rows,
        key=lambda r: (
            not r["golden_candidate"],
            -r["count"],
            r["merchant"],
            r["failure_mode"],
        ),
    )


def handle_digest(params: dict) -> dict[str, Any]:
    requested = (params.get("pass_id", [""])[0] or "").strip()
    record = _select_pass(requested or None)
    passes = [entry["pass_id"] for entry in _list_passes()]
    if record is None:
        return {
            "pass_id": None,
            "groups": [],
            "passes": passes,
            "frozen": _frozen_classes(),
            "generated_at": None,
            "source": None,
            "error": (
                f"no pass {requested!r} in {VERDICT_DIR}"
                if requested
                else None
            ),
        }

    pass_id = record["pass_id"]
    approvals = _read_approvals(pass_id)
    digest_path = record.get("digest_path")
    # The verdicts file is always read: it is where the money lives, even
    # when the adjudicator also wrote a pre-grouped digest.
    entries, warning = _pass_entries(record)
    by_key = {(e["image_id"], e["receipt_id"]): e for e in entries}
    generated_at, source = None, None
    groups: list[dict[str, Any]] = []

    if digest_path is not None:
        payload, error = _read_json(digest_path)
        if error is not None:
            warning = warning or error
        else:
            rows = (
                _first(payload, "t1_groups", "groups")
                if isinstance(payload, dict)
                else payload
            )
            groups = [
                group
                for group in (
                    _normalize_group(row, by_key)
                    for row in (rows if isinstance(rows, list) else [])
                )
                if group is not None
            ]
            if isinstance(payload, dict):
                generated_at = _optional_str(
                    _first(payload, "generated_at", "built_at")
                )
            source = digest_path.name
    if not groups:
        # No pre-grouped digest: group the batch tier out of the verdicts
        # file itself, so the screen works the moment a pass lands.
        groups = _derive_digest(entries)
        source = source or (
            record["verdicts_path"].name
            if record.get("verdicts_path")
            else None
        )

    return {
        "pass_id": pass_id,
        "groups": _finalize_groups(groups, approvals),
        "passes": passes,
        "frozen": _frozen_classes(),
        "generated_at": generated_at,
        "source": source,
        "warning": warning,
    }


def handle_approve_post(body: dict) -> dict[str, Any]:
    pass_id = str(body.get("pass_id", "") or "").strip()
    group_id = str(body.get("group_id", "") or "").strip()
    if not group_id:
        return {"error": "group_id is required"}
    record = _select_pass(pass_id or None)
    if record is None:
        return {"error": f"no pass {pass_id!r} in {VERDICT_DIR}"}
    pass_id = record["pass_id"]
    path = _approvals_path(pass_id)
    if path is None:
        return {"error": f"invalid pass id {pass_id!r}"}

    digest = handle_digest({"pass_id": [pass_id]})
    group = next(
        (g for g in digest["groups"] if g["group_id"] == group_id), None
    )
    if group is None:
        return {"error": f"no group {group_id!r} in pass {pass_id}"}
    if group["frozen"]:
        return {
            "error": (
                f"class {group['failure_mode']!r} is frozen by a failed "
                "blind audit; clear the freeze marker before approving."
            )
        }

    with _review_lock:
        approvals = _read_approvals(pass_id)
        approved = approvals["approved_groups"]
        if group_id in approved:
            return {
                "ok": True,
                "already": True,
                "pass_id": pass_id,
                "group_id": group_id,
                "approvals": len(approved),
                "path": str(path),
            }
        approved.append(group_id)
        approvals["approval_log"].append(
            {
                "group_id": group_id,
                "merchant": group["merchant"],
                "failure_mode": group["failure_mode"],
                "action": group["action"],
                "golden_candidate": group["golden_candidate"],
                "receipts": group["receipts"],
                "approved_by": str(body.get("author", "user") or "user"),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        approvals["pass_id"] = pass_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(approvals, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "ok": True,
        "already": False,
        "pass_id": pass_id,
        "group_id": group_id,
        "approvals": len(approved),
        "path": str(path),
    }


def _audit_sample(pass_id: str, entries: list[dict[str, Any]]) -> list[dict]:
    """A stable blind sample of the auto-applied tier.

    Seeded on the pass id so reloading the deck never reshuffles it, and so
    the sample is reproducible from the pass alone when auditing the audit.
    """
    auto = sorted(
        (e for e in entries if e["tier"] == "T0"),
        key=lambda e: (e["image_id"], e["receipt_id"]),
    )
    if not auto:
        return []
    size = min(
        len(auto), max(AUDIT_MIN_SAMPLE, math.ceil(len(auto) * AUDIT_FRACTION))
    )
    chosen = random.Random(pass_id).sample(range(len(auto)), size)
    return [auto[position] for position in sorted(chosen)]


def _blind_dossier(dossier: Optional[dict[str, Any]]) -> Optional[dict]:
    """The scout's observations without any of its conclusions.

    An audit only means something if the human reaches a verdict the agent
    could not have suggested, so diagnosis, proposal, confidence and the
    recommendation itself are withheld until the verdict is committed.
    """
    if dossier is None:
        return None
    return {
        "failure_mode": None,
        "diagnosis": "",
        "evidence": dossier.get("evidence", []),
        "proposal": None,
        "abstain_reason": None,
        "verdict_recommendation": None,
        "confidence": None,
        "signals_concurring": [],
        "generated_at": dossier.get("generated_at"),
        "author": dossier.get("author"),
        "source": dossier.get("source"),
        "blind": True,
    }


def _audited_receipts(pass_id: str) -> set[tuple[str, int]]:
    return {
        (entry.get("image_id"), entry.get("receipt_id"))
        for entry in _read_reviews()
        if entry.get("verdict") in AUDIT_VERDICTS
        and entry.get("pass_id") == pass_id
    }


def handle_audit(params: dict) -> dict[str, Any]:
    requested = (params.get("pass_id", [""])[0] or "").strip()
    record = _select_pass(requested or None)
    frozen = _frozen_classes()
    if record is None:
        return {
            "pass_id": None,
            "sample": [],
            "size": 0,
            "total_auto": 0,
            "frozen": frozen,
            "error": (
                f"no pass {requested!r} in {VERDICT_DIR}" if requested else None
            ),
        }
    pass_id = record["pass_id"]
    entries, warning = _pass_entries(record)
    sample = _audit_sample(pass_id, entries)

    image_id = (params.get("image_id", [""])[0] or "").strip()
    if image_id:
        try:
            receipt_id = int(params.get("receipt_id", [""])[0])
        except (TypeError, ValueError):
            return {"error": "receipt_id must be an integer"}
        if not any(
            e["image_id"] == image_id and e["receipt_id"] == receipt_id
            for e in sample
        ):
            return {
                "error": (
                    f"receipt {receipt_id} is not in the blind sample for "
                    f"pass {pass_id}"
                )
            }
        detail = handle_receipt(
            {"image_id": [image_id], "receipt_id": [str(receipt_id)]}
        )
        if "error" in detail:
            return detail
        return {
            **detail,
            "pass_id": pass_id,
            "blind": True,
            "dossier": _blind_dossier(detail.get("dossier")),
            "reviews": [
                r
                for r in detail.get("reviews", [])
                if r.get("verdict") not in AUDIT_VERDICTS
            ],
        }

    reviewed = _audited_receipts(pass_id)
    return {
        "pass_id": pass_id,
        "size": len(sample),
        "total_auto": sum(1 for e in entries if e["tier"] == "T0"),
        "frozen": frozen,
        "warning": warning,
        "sample": [
            {
                "image_id": entry["image_id"],
                "receipt_id": entry["receipt_id"],
                "merchant": entry["merchant"],
                "reviewed": (entry["image_id"], entry["receipt_id"])
                in reviewed,
            }
            for entry in sample
        ],
    }


def handle_verdicts(params: dict) -> dict[str, Any]:
    requested = (params.get("pass_id", [""])[0] or "").strip()
    record = _select_pass(requested or None)
    frozen = _frozen_classes()
    if record is None:
        return {
            "pass_id": None,
            "entries": [],
            "frozen": frozen,
            "passes": [],
            "error": (
                f"no pass {requested!r} in {VERDICT_DIR}" if requested else None
            ),
        }
    entries, warning = _pass_entries(record)
    frozen_set = set(frozen)
    return {
        "pass_id": record["pass_id"],
        "passes": [entry["pass_id"] for entry in _list_passes()],
        "frozen": frozen,
        "warning": warning,
        # A frozen entry is still served — the writer needs to see what it
        # must not apply, not have it quietly disappear.
        "entries": [
            {
                **entry,
                "frozen": bool(
                    frozen_set
                    & {_freeze_class(entry["failure_mode"]), entry["tier"]}
                ),
            }
            for entry in entries
        ],
    }


def _write_freeze(
    mode: Optional[str],
    tier: Optional[str],
    entry: dict[str, Any],
    pass_id: Optional[str],
) -> list[str]:
    """Freeze the audited verdict's tier and its class, per the operating
    model ("a marker file in .dev-harness/freeze/<tier-or-class>").

    Markers are named exactly the way ``agentic_adjudicate.load_frozen``
    matches them — a tier name or a bare A-J class letter. A marker it
    cannot match is a freeze that silently does nothing, so the tier is
    always written even when the mode yields no usable class letter.
    """
    names = []
    for name in (_freeze_class(mode), tier):
        if not name or name == "unclassified" or name in names:
            continue
        names.append(name)
    if not names:
        names = ["T0"]

    FREEZE_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        (FREEZE_DIR / name).write_text(
            json.dumps(
                {
                    "marker": name,
                    "mode": mode,
                    "tier": tier,
                    "pass_id": pass_id,
                    "image_id": entry["image_id"],
                    "receipt_id": entry["receipt_id"],
                    "note": entry.get("note", ""),
                    "frozen_at": datetime.now(timezone.utc).isoformat(),
                    "reason": (
                        "blind audit disagreed with an auto-applied verdict"
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return names


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

    revealed: Optional[dict[str, Any]] = None
    froze: list[str] = []
    if verdict in AUDIT_VERDICTS:
        record = _select_pass(
            _optional_str(body.get("pass_id"))
        )
        pass_id = record["pass_id"] if record else None
        entry["pass_id"] = pass_id
        adjudicated = None
        if record is not None:
            entries, _ = _pass_entries(record)
            adjudicated = next(
                (
                    e
                    for e in entries
                    if e["image_id"] == image_id
                    and e["receipt_id"] == receipt_id
                ),
                None,
            )
        dossier, _ = _read_dossier(image_id, receipt_id)
        # What the human was not allowed to see while deciding.
        revealed = {
            "tier": (adjudicated or {}).get("tier"),
            "reason": (adjudicated or {}).get("reason"),
            "failure_mode": (dossier or {}).get("failure_mode")
            or (adjudicated or {}).get("failure_mode"),
            "diagnosis": (dossier or {}).get("diagnosis")
            or (adjudicated or {}).get("diagnosis"),
            "verdict_recommendation": (dossier or {}).get(
                "verdict_recommendation"
            )
            or (adjudicated or {}).get("verdict_recommendation"),
            "confidence": (dossier or {}).get("confidence")
            or (adjudicated or {}).get("confidence"),
            "signals_concurring": (dossier or {}).get("signals_concurring")
            or [],
            "proposal": (dossier or {}).get("proposal")
            or (adjudicated or {}).get("proposal"),
            "abstain_reason": (dossier or {}).get("abstain_reason"),
        }
        entry["revealed_failure_mode"] = revealed["failure_mode"]
        if verdict == "audit-disagree":
            # One disagreement is enough. The class comes from the
            # adjudicated entry first: that is the exact string the next
            # adjudication run will classify, so freezing anything else
            # would leave the tier open.
            froze = _write_freeze(
                (adjudicated or {}).get("failure_mode")
                or revealed["failure_mode"],
                (adjudicated or {}).get("tier"),
                entry,
                pass_id,
            )
            entry["froze"] = froze

    with _review_lock:
        REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REVIEW_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    return {
        "ok": True,
        "entry": entry,
        "log": str(REVIEW_LOG),
        "revealed": revealed,
        "freeze_written": froze,
        "frozen": _frozen_classes(),
    }


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
            if route == "/digest":
                self._send(200, handle_digest(params))
                return
            if route == "/verdicts":
                self._send(200, handle_verdicts(params))
                return
            if route == "/audit":
                payload = handle_audit(params)
                # An empty deck is a normal state, not a client error; only a
                # named-but-missing pass or a bad id is a 400.
                self._send(400 if payload.get("error") else 200, payload)
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
                        "verdict_dir": str(VERDICT_DIR),
                        "approvals": str(APPROVAL_DIR),
                        "freeze": str(FREEZE_DIR),
                        "frozen": _frozen_classes(),
                        "passes": [p["pass_id"] for p in _list_passes()],
                        "verdicts": list(REVIEW_VERDICTS),
                        "routes": [
                            "/merchants",
                            "/queues",
                            "/worklist",
                            "/receipt",
                            "/digest",
                            "/verdicts",
                            "/audit",
                            "/review",
                            "/approve",
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
        if route == "/approve":
            payload = handle_approve_post(body)
            self._send(400 if "error" in payload else 200, payload)
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
