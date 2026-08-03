#!/usr/bin/env python3
"""P2 adjudicator: route dossier v2 files into trust tiers.

Files, never rows: this script reads `.dev-harness/dossiers/*.json`
(schema in docs/line-items/agentic-review/RUNBOOK_TRIAGE.md), routes
each dossier per the operating model's tiers, and writes
`.dev-harness/verdicts/<pass-id>.jsonl` plus a grouped
`.dev-harness/verdicts/<pass-id>.digest.json` for the T1 batch-approval
screen. It never touches DynamoDB.

Tiers (docs/line-items/agentic-review/OPERATING_MODEL.md):

T0 auto-apply   ALL of: contiguous extension, guard-verified with
                post-state `match`, vision confirms every added line is
                a product, and at most --t0-limit (default 5) per pass;
                overflow demotes to T1.
T1 batch        merchant x mode groups awaiting one Approve each;
                includes golden candidates, which require
                signals_concurring to cover {arithmetic, bank, vision}
                and are NEVER auto-applied.
T2 escalation   image_suspect, destructive actions (incl. duplicate
                groups), J-unknown mode, and any `flag` on a green
                (match) row.
abstain         everything else; an honest abstention beats a guess.

Freeze markers: a file named after a tier ("T0", "T1") or a mode class
letter ("H", "B", ...) in `.dev-harness/freeze/` demotes every
actionable verdict of that class to T2 until the marker is removed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = REPO_ROOT / ".dev-harness"
DEFAULT_DOSSIER_DIR = HARNESS_DIR / "dossiers"
DEFAULT_VERDICTS_DIR = HARNESS_DIR / "verdicts"
DEFAULT_FREEZE_DIR = HARNESS_DIR / "freeze"

TIER_T0 = "T0"
TIER_T1 = "T1"
TIER_T2 = "T2"
TIER_ABSTAIN = "abstain"

DEFAULT_T0_LIMIT = 5
REQUIRED_GOLDEN_SIGNALS = frozenset({"arithmetic", "bank", "vision"})


def mode_class(dossier: dict[str, Any]) -> str:
    """The A-J taxonomy letter of a dossier's mode ('?' when absent)."""
    mode = str(dossier.get("mode") or "").strip()
    return mode.split("-", 1)[0].upper() if mode else "?"


def group_id(dossier: dict[str, Any]) -> str:
    """Stable merchant x mode group key for the T1 batch digest."""

    def slug(value: Optional[str], fallback: str) -> str:
        text = str(value or "").strip().lower() or fallback
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or fallback

    return (
        f"{slug(dossier.get('merchant'), 'unknown-merchant')}"
        f"::{slug(dossier.get('mode'), 'unknown-mode')}"
    )


def _proposal_t0_eligible(proposal: Optional[dict[str, Any]]) -> bool:
    if not isinstance(proposal, dict):
        return False
    after = proposal.get("after") or {}
    return bool(
        proposal.get("verified")
        and proposal.get("contiguous")
        and after.get("status") == "match"
        and proposal.get("vision_products_confirmed") is True
    )


def route_dossier(dossier: dict[str, Any]) -> tuple[str, str, bool]:
    """(tier, reason, golden) for one dossier, before freeze/limits."""
    recon = dossier.get("recon") or {}
    recommendation = dossier.get("verdict_recommendation")
    proposal = dossier.get("proposal")

    # T2 escalations trump everything: these are exactly the calls the
    # operating model reserves for a human.
    if dossier.get("image_suspect"):
        return TIER_T2, "image_suspect", False
    if dossier.get("destructive") or dossier.get("duplicate_group"):
        return TIER_T2, "destructive", False
    if mode_class(dossier) == "J":
        return TIER_T2, "j-unknown", False
    if recommendation == "flag" and recon.get("status") == "match":
        # A flag on a green row means the tolerance ladder is producing
        # false accepts; every downstream count is suspect.
        return TIER_T2, "flag-on-green", False

    if recommendation == "golden":
        signals = set(dossier.get("signals_concurring") or [])
        if signals >= REQUIRED_GOLDEN_SIGNALS:
            # Golden candidates are T1-only and never auto-applied.
            return TIER_T1, "golden-candidate", True
        return TIER_ABSTAIN, "golden-insufficient-signals", False

    if _proposal_t0_eligible(proposal):
        return TIER_T0, "auto-extension", False

    verified = isinstance(proposal, dict) and proposal.get("verified")
    if verified:
        return TIER_T1, "guarded-extension", False
    if recommendation == "approve-fix":
        # An approve-fix with no guard-verified proposal cannot be
        # applied by the writer; guessing would fork the guard.
        return TIER_ABSTAIN, "approve-fix-without-verified-proposal", False
    if recommendation == "confirm":
        return TIER_ABSTAIN, "confirm-no-action", False
    if recommendation == "flag":
        return TIER_ABSTAIN, "flag-no-safe-fix", False
    return TIER_ABSTAIN, "no-verdict", False


def load_frozen(freeze_dir: Path) -> set[str]:
    """Marker names in the freeze dir (tier names or class letters)."""
    if not freeze_dir.is_dir():
        return set()
    return {p.name for p in freeze_dir.iterdir() if not p.name.startswith(".")}


def adjudicate(
    dossiers: list[tuple[str, dict[str, Any]]],
    pass_id: str,
    frozen: set[str],
    t0_limit: int = DEFAULT_T0_LIMIT,
) -> list[dict[str, Any]]:
    """Route every dossier; apply freeze demotions and the T0 cap.

    ``dossiers`` is a list of (source file name, dossier dict); routing
    is deterministic in that order.
    """
    entries = []
    t0_count = 0
    for source, dossier in dossiers:
        tier, reason, golden = route_dossier(dossier)

        if tier in (TIER_T0, TIER_T1):
            cls = mode_class(dossier)
            frozen_hit = next(
                (name for name in (tier, cls) if name in frozen), None
            )
            if frozen_hit is not None:
                tier, reason = TIER_T2, f"frozen:{frozen_hit}"
                golden = False

        if tier == TIER_T0:
            if t0_count >= t0_limit:
                tier, reason = TIER_T1, "t0-overflow"
            else:
                t0_count += 1

        entries.append(
            {
                "pass_id": pass_id,
                "image_id": dossier.get("image_id"),
                "receipt_id": dossier.get("receipt_id"),
                "tier": tier,
                "reason": reason,
                "golden": golden,
                "group_id": group_id(dossier),
                "merchant": dossier.get("merchant"),
                "mode": dossier.get("mode"),
                "proposal": dossier.get("proposal"),
                "duplicate_group": dossier.get("duplicate_group"),
                "verdict_by": dossier.get("verdict_by"),
                "dossier_file": source,
            }
        )
    return entries


def build_digest(
    entries: list[dict[str, Any]], pass_id: str
) -> dict[str, Any]:
    """Grouped T1 view: one row per merchant x mode group."""
    groups: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry["tier"] != TIER_T1:
            continue
        group = groups.setdefault(
            entry["group_id"],
            {
                "group_id": entry["group_id"],
                "merchant": entry["merchant"],
                "mode": entry["mode"],
                "receipts": [],
                "golden_count": 0,
            },
        )
        group["receipts"].append(
            {
                "image_id": entry["image_id"],
                "receipt_id": entry["receipt_id"],
                "reason": entry["reason"],
                "golden": entry["golden"],
            }
        )
        if entry["golden"]:
            group["golden_count"] += 1

    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["tier"]] = counts.get(entry["tier"], 0) + 1

    return {
        "pass_id": pass_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "t1_groups": sorted(groups.values(), key=lambda g: g["group_id"]),
    }


def load_dossiers(dossier_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    dossiers = []
    for path in sorted(dossier_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"skipping unreadable dossier {path}: {exc}", file=sys.stderr
            )
            continue
        if not isinstance(data, dict):
            print(f"skipping non-object dossier {path}", file=sys.stderr)
            continue
        dossiers.append((path.name, data))
    return dossiers


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pass-id", required=True)
    parser.add_argument(
        "--dossier-dir", type=Path, default=DEFAULT_DOSSIER_DIR
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_VERDICTS_DIR)
    parser.add_argument("--freeze-dir", type=Path, default=DEFAULT_FREEZE_DIR)
    parser.add_argument("--t0-limit", type=int, default=DEFAULT_T0_LIMIT)
    args = parser.parse_args(argv)

    dossiers = load_dossiers(args.dossier_dir)
    if not dossiers:
        print(f"no dossiers found in {args.dossier_dir}", file=sys.stderr)
        return 1

    frozen = load_frozen(args.freeze_dir)
    if frozen:
        print(f"active freeze markers: {sorted(frozen)}", file=sys.stderr)

    entries = adjudicate(
        dossiers, args.pass_id, frozen, t0_limit=args.t0_limit
    )
    digest = build_digest(entries, args.pass_id)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    verdicts_path = args.out_dir / f"{args.pass_id}.jsonl"
    digest_path = args.out_dir / f"{args.pass_id}.digest.json"
    with verdicts_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, default=str) + "\n")
    digest_path.write_text(
        json.dumps(digest, indent=2, default=str) + "\n", encoding="utf-8"
    )

    print(
        f"routed {len(entries)} dossiers -> {verdicts_path} "
        f"(counts: {digest['counts']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
