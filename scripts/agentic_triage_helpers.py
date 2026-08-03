#!/usr/bin/env python3
"""Deterministic per-receipt digest for the vision-scout triage pass.

READ-ONLY against DynamoDB. This is the P1 helper from
docs/line-items/agentic-review/OPERATING_MODEL.md: it gives a triage
agent everything the data can say about one receipt in a single tool
call — words/lines with section membership, the decoded line items vs
the summary baseline, the summary's tender/bank fields, and every
plausible ITEMS-boundary extension candidate already dry-run simulated
through the REAL arithmetic guard.

The guard math is not reimplemented here. Candidates are evaluated by
``extend_items_section_impl`` loaded straight out of
``scripts/receipt_mcp_server.py`` (the same importlib pattern as the
dev harness's validation shim), so this helper and the MCP tool can
never disagree. Every simulation runs with ``dry_run=True``; nothing
in this module writes anywhere except stdout and, on request, a
dossier-skeleton file.

Usage:
    python scripts/agentic_triage_helpers.py \
        --image-id <uuid> --receipt-id 1            # JSON digest
    python scripts/agentic_triage_helpers.py \
        --image-id <uuid> --receipt-id 1 \
        --emit-dossier-skeleton .dev-harness/dossiers
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "receipt_upload"))
sys.path.insert(0, str(REPO_ROOT / "receipt_dynamo"))

from receipt_upload.line_items.geometry import is_proven  # noqa: E402

DEFAULT_TABLE = "ReceiptsTable-dc5be22"  # dev; this helper is read-only
DEFAULT_MAX_SIMULATIONS = 12
DOSSIER_SCHEMA = "dossier-v2"


def _ensure_mcp_importable() -> None:
    """Register a minimal fake ``mcp`` package when it is absent.

    The MCP server module imports ``mcp`` at module scope, but this
    helper only uses its ``*_impl`` functions. A fresh venv (or one
    with an incompatible mcp 2.x) must not block read-only triage.
    """
    try:
        import mcp.server.stdio  # noqa: F401
        import mcp.types  # noqa: F401

        return
    except ImportError:
        pass

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    stdio_mod = types.ModuleType("mcp.server.stdio")
    types_mod = types.ModuleType("mcp.types")

    class _StubServer:
        def __init__(self, name: str):
            self.name = name

        def list_tools(self):
            def decorator(func):
                return func

            return decorator

        def call_tool(self):
            def decorator(func):
                return func

            return decorator

    def _stub_stdio_server(*_args, **_kwargs):  # pragma: no cover
        raise RuntimeError("stdio transport unavailable (stubbed mcp)")

    class _StubContent:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    server_mod.Server = _StubServer
    stdio_mod.stdio_server = _stub_stdio_server
    types_mod.Tool = _StubContent
    types_mod.TextContent = _StubContent
    types_mod.ImageContent = _StubContent
    mcp_mod.server = server_mod

    sys.modules.setdefault("mcp", mcp_mod)
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.stdio"] = stdio_mod
    sys.modules["mcp.types"] = types_mod


def load_mcp_server(module_name: str = "_agentic_mcp"):
    """Import scripts/receipt_mcp_server.py as a module.

    Same pattern as portfolio/dev-harness/validation_shim.py: the file
    is not an installed package, so it is loaded by path. Cached in
    sys.modules under ``module_name``.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    _ensure_mcp_importable()
    path = REPO_ROOT / "scripts" / "receipt_mcp_server.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


def _not_found_error():
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    return EntityNotFoundError


def _line_texts(details) -> dict[int, str]:
    """line_id -> joined word text, in reading order."""
    by_line: dict[int, list] = {}
    for word in getattr(details, "words", None) or []:
        by_line.setdefault(int(word.line_id), []).append(word)
    texts = {}
    for line_id, words in by_line.items():
        words.sort(key=lambda w: getattr(w, "word_id", 0))
        texts[line_id] = " ".join(str(w.text or "") for w in words)
    return texts


def enumerate_extension_candidates(
    all_line_ids: list[int], sections: list
) -> list[dict[str, Any]]:
    """Candidate ITEMS extensions: runs of unclaimed lines.

    A candidate is ``contiguous`` when its run touches the current
    ITEMS span directly (no claimed or missing line between). Adjacent
    runs are grown cumulatively from the ITEMS side outward so the
    smallest sufficient extension is simulated first; non-adjacent
    runs get a single whole-run candidate (recorded non-contiguous).
    """
    items = next(
        (s for s in sections or [] if s.section_type == "ITEMS"), None
    )
    if items is None or not items.line_ids:
        return []
    claimed: set[int] = set()
    for section in sections or []:
        claimed |= {int(x) for x in section.line_ids or []}
    unclaimed = sorted(set(int(x) for x in all_line_ids) - claimed)
    if not unclaimed:
        return []

    items_lids = sorted(int(x) for x in items.line_ids)
    lo, hi = items_lids[0], items_lids[-1]

    runs: list[list[int]] = []
    for lid in unclaimed:
        if runs and lid == runs[-1][-1] + 1:
            runs[-1].append(lid)
        else:
            runs.append([lid])

    candidates: list[dict[str, Any]] = []
    for run in runs:
        if run[0] == hi + 1:  # grows the zone downward on the page
            for end in range(len(run)):
                candidates.append(
                    {"add_line_ids": run[: end + 1], "contiguous": True}
                )
        elif run[-1] == lo - 1:  # grows the zone upward
            for start in range(len(run) - 1, -1, -1):
                candidates.append(
                    {"add_line_ids": run[start:], "contiguous": True}
                )
        else:
            candidates.append({"add_line_ids": list(run), "contiguous": False})
    return candidates


def simulate_candidates(
    mcp_server,
    client,
    image_id: str,
    receipt_id: int,
    candidates: list[dict[str, Any]],
    max_simulations: int = DEFAULT_MAX_SIMULATIONS,
) -> list[dict[str, Any]]:
    """Dry-run each candidate through the real guard. Read-only."""
    ordered = sorted(candidates, key=lambda c: not c["contiguous"])
    simulated = []
    for cand in ordered[:max_simulations]:
        result = _run(
            mcp_server.extend_items_section_impl(
                client,
                image_id,
                receipt_id,
                cand["add_line_ids"],
                dry_run=True,
            )
        )
        entry = dict(cand)
        if "error" in result:
            entry.update(verified=False, refusal=result["error"])
        else:
            entry.update(
                verified=bool(result.get("verified")),
                refusal=result.get("refusal"),
                before=_zone_view(result.get("before")),
                after=_zone_view(result.get("after")),
            )
        simulated.append(entry)
    return simulated


def _zone_view(zone: Optional[dict]) -> Optional[dict]:
    if not isinstance(zone, dict):
        return None
    return {
        "status": zone.get("status"),
        "items_sum": zone.get("items_sum"),
        "baseline": zone.get("baseline"),
        "delta": zone.get("delta"),
        "n_items": zone.get("n_items"),
    }


def build_digest(
    client,
    image_id: str,
    receipt_id: int,
    mcp_server=None,
    max_simulations: int = DEFAULT_MAX_SIMULATIONS,
) -> dict[str, Any]:
    """One JSON document with everything the data can say."""
    mcp_server = mcp_server or load_mcp_server()
    not_found = _not_found_error()

    line_items_view = _run(
        mcp_server.get_receipt_line_items_impl(client, image_id, receipt_id)
    )

    details = client.get_receipt_details(image_id, receipt_id)
    try:
        sections = (
            client.get_receipt_sections_from_receipt(image_id, receipt_id)
            or []
        )
    except not_found:
        sections = []

    section_of: dict[int, str] = {}
    for section in sections:
        for lid in section.line_ids or []:
            section_of[int(lid)] = section.section_type

    texts = _line_texts(details)
    all_line_ids = sorted(
        {int(line.line_id) for line in getattr(details, "lines", None) or []}
        | set(texts)
    )
    lines = [
        {
            "line_id": lid,
            "text": texts.get(lid, ""),
            "section": section_of.get(lid),
        }
        for lid in all_line_ids
    ]

    summary_view = None
    try:
        record = client.get_receipt_summary(image_id, receipt_id)
    except not_found:
        record = None
    if record is not None:
        summary_view = {
            "merchant_name": getattr(record, "merchant_name", None),
            "subtotal": getattr(record, "subtotal", None),
            "tax": getattr(record, "tax", None),
            "grand_total": getattr(record, "grand_total", None),
            "tip": getattr(record, "tip", None),
            "tender_class": getattr(record, "tender_class", None),
            "bank_amount": getattr(record, "bank_amount", None),
            "bank_match_confidence": getattr(
                record, "bank_match_confidence", None
            ),
            "timestamp_computed": getattr(record, "timestamp_computed", None),
        }

    recon_status = line_items_view.get("reconciliation_status")
    proven = is_proven(
        recon_status,
        summary_view.get("grand_total") if summary_view else None,
        summary_view.get("bank_amount") if summary_view else None,
    )

    candidates = enumerate_extension_candidates(all_line_ids, sections)
    simulated = simulate_candidates(
        mcp_server,
        client,
        image_id,
        receipt_id,
        candidates,
        max_simulations=max_simulations,
    )
    best = next(
        (c for c in simulated if c.get("verified") and c.get("contiguous")),
        next((c for c in simulated if c.get("verified")), None),
    )

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "lines": lines,
        "sections": [
            {
                "section_type": s.section_type,
                "line_ids": sorted(int(x) for x in s.line_ids or []),
                "validation_status": s.validation_status or "NONE",
                "model_source": s.model_source,
            }
            for s in sections
        ],
        "line_items": line_items_view,
        "summary": summary_view,
        "is_proven": proven,
        "extension_candidates": simulated,
        "extension_candidates_total": len(candidates),
        "best_extension": best,
    }


def dossier_skeleton(digest: dict[str, Any]) -> dict[str, Any]:
    """Pre-filled dossier v2 stub (RUNBOOK_TRIAGE.md schema)."""
    summary = digest.get("summary") or {}
    li = digest.get("line_items") or {}
    best = digest.get("best_extension")
    proposal = None
    if best is not None:
        proposal = {
            "add_line_ids": best.get("add_line_ids"),
            "contiguous": best.get("contiguous"),
            "verified": best.get("verified"),
            "before": best.get("before"),
            "after": best.get("after"),
            "vision_products_confirmed": None,
        }
    return {
        "schema": DOSSIER_SCHEMA,
        "image_id": digest["image_id"],
        "receipt_id": digest["receipt_id"],
        "merchant": summary.get("merchant_name"),
        "mode": None,
        "recon": {
            "status": li.get("reconciliation_status"),
            "items_sum": li.get("items_sum"),
            "baseline": (
                li.get("items_sum") - li.get("delta")
                if li.get("items_sum") is not None
                and li.get("delta") is not None
                else None
            ),
            "delta": li.get("delta"),
        },
        "bank": {
            "amount": summary.get("bank_amount"),
            "match_confidence": summary.get("bank_match_confidence"),
            "tip": summary.get("tip"),
            "tender_class": summary.get("tender_class"),
        },
        "image_suspect": False,
        "destructive": False,
        "duplicate_group": None,
        "proposal": proposal,
        "visual_evidence": [],
        "verdict_recommendation": None,
        "confidence": None,
        "confidence_justification": None,
        "signals_concurring": [],
        "verdict_by": None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--receipt-id", required=True, type=int)
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"DynamoDB table (default: {DEFAULT_TABLE}; read-only)",
    )
    parser.add_argument(
        "--max-simulations",
        type=int,
        default=DEFAULT_MAX_SIMULATIONS,
        help="Cap on dry-run guard simulations",
    )
    parser.add_argument(
        "--emit-dossier-skeleton",
        metavar="DIR",
        default=None,
        help="Also write a dossier v2 stub into DIR",
    )
    args = parser.parse_args(argv)

    from receipt_dynamo import DynamoClient

    client = DynamoClient(args.table)
    digest = build_digest(
        client,
        args.image_id,
        args.receipt_id,
        max_simulations=args.max_simulations,
    )
    json.dump(digest, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")

    if args.emit_dossier_skeleton:
        out_dir = Path(args.emit_dossier_skeleton)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.image_id}-{args.receipt_id}.json"
        if out_path.exists():
            print(
                f"refusing to overwrite existing dossier {out_path}",
                file=sys.stderr,
            )
            return 1
        out_path.write_text(
            json.dumps(dossier_skeleton(digest), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"wrote dossier skeleton: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
