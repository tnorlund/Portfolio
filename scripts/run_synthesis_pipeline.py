#!/usr/bin/env python3
"""Synthetic receipt pipeline orchestrator.

Runs the full loop for a merchant list:
  1. Export each merchant's receipts from DynamoDB via ReceiptPlace
     (ReceiptMetadata is DEPRECATED) into per-merchant subdirs.
  2. Run ONE combined local synthesis pipeline over all exported dirs, producing
     a single artifact set + bundle. Mix-balance is a cross-merchant
     concentration signal, so the proven backbone uses one combined bundle
     rather than one bundle per merchant (a single merchant always self-
     concentrates and reads as medium/high balance risk).
  3. Print a consolidated cross-merchant summary plus a per-merchant breakdown
     (accepted counts by op, taxable edits + rates, rejection reasons).

This is the INTEGRATION layer: it CALLS the deterministic synthesis pipeline
and gates; it does not edit synthesis internals or decide what passes.

Feature flags:
  --research-dir   [Branch 1] Read merchant_intelligence/<slug>.json when present.
                   Falls back to hardcoded merchant_tax_config + online_catalogs.
  --render         [Branch 2] Emit QA images per accepted candidate via the
                   receipt-font-render renderer (not yet implemented; no-ops today).

Usage (no-spend, backbone only):
    export RECEIPT_AGENT_DISABLE_PAID_LLM=1
    export DISABLE_PAID_LLM=1
    export DYNAMO_TABLE_NAME=ReceiptsTable-dc5be22
    export AWS_REGION=us-east-1

    python3.12 scripts/run_synthesis_pipeline.py \\
      --merchants "Vons" "Sprouts Farmers Market" "Amazon Fresh" "Target" \\
      --output-dir .tmp/pipeline-run \\
      --max-candidates 80 \\
      --max-per-merchant 100 \\
      --max-per-merchant-operation 60 \\
      --min-grounded-candidate-share 0.0

Acceptance test: 5 Vons taxable add_line_item candidates appear in the bundle
at 7.25%, mix-balance risk low.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "receipt_agent"))
sys.path.insert(0, str(PROJECT_ROOT / "receipt_layoutlm"))
# Prefer this worktree's receipt_dynamo over any editable install pointing at a
# different worktree, so DynamoClient/export_image come from the checked-out code.
sys.path.insert(0, str(PROJECT_ROOT / "receipt_dynamo"))

# The shared export_image utility constructs its DynamoDB client at this region
# when it does not accept an explicit region argument.
_EXPORT_IMAGE_DEFAULT_REGION = "us-east-1"

# These are populated lazily so the script can --help without full imports.
_dynamo = None
_replay = None


def _export_image_supports_region(export_image) -> bool:
    """True if the installed export_image accepts a ``region`` argument.

    The orchestrator passes ``region`` through when supported; when not, it only
    proceeds for the default region (validated up front in run_pipeline) so the
    export reads the same table the ReceiptPlace lookup used.
    """
    import inspect

    try:
        return "region" in inspect.signature(export_image).parameters
    except (TypeError, ValueError):
        return False


def _import_dynamo():
    global _dynamo
    if _dynamo is None:
        from receipt_dynamo import DynamoClient, export_image
        _dynamo = (DynamoClient, export_image)
    return _dynamo


def _import_replay():
    global _replay
    if _replay is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "verify_synthetic_replay",
            PROJECT_ROOT / "scripts" / "verify_synthetic_replay.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _replay = mod
    return _replay


# Merchants with receipt-validated taxable-edit clearance (CONTEXT.md §What shipped).
DEFAULT_MERCHANTS = [
    "Vons",
    "Sprouts Farmers Market",
    "Amazon Fresh",
    "Target",
]


def _slug(name: str) -> str:
    cleaned = re.sub(r"['''ʼ`]", "", str(name or "").lower())
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Branch 1 hook: merchant intelligence artifacts
# ---------------------------------------------------------------------------

def _load_research_artifact(merchant_name: str, research_dir: str | None) -> dict[str, Any] | None:
    """Load Branch-1 intelligence artifact when present; else return None."""
    if not research_dir:
        return None
    slug = _slug(merchant_name)
    path = Path(research_dir) / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [research] WARNING: could not load {path}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Branch 2 hook: receipt rendering
# ---------------------------------------------------------------------------

def _taxable_adds_by_merchant(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group accepted taxable add_line_item candidates by merchant from a bundle.

    Taxable adds are not a distinct operation key — they are ``add_line_item``
    candidates whose ``added_item.taxable`` is True and whose
    ``arithmetic_reconciliation`` recomputed TAX at a receipt-validated rate.
    Per merchant we count them, the distinct applied rates, the base receipts
    they edited, and (for honesty) how many lack a recorded ``tax_rate`` — the
    acceptance signal is specifically rate-qualified (5 Vons adds at 0.0725), so
    a taxable add with no rate is surfaced rather than silently folded in.
    """
    by_merchant: dict[str, dict[str, Any]] = {}
    for example in bundle.get("synthetic_training_examples") or []:
        metadata = example.get("metadata") or {}
        if metadata.get("operation") != "add_line_item":
            continue
        added_item = metadata.get("added_item") or {}
        if added_item.get("taxable") is not True:
            continue
        merchant = str(example.get("merchant_name") or "unknown")
        entry = by_merchant.setdefault(
            merchant,
            {
                "taxable_add_count": 0,
                "taxable_add_missing_rate_count": 0,
                "_rates": {},
                "_base_receipts": set(),
            },
        )
        entry["taxable_add_count"] += 1
        recon = metadata.get("arithmetic_reconciliation") or {}
        rate = recon.get("tax_rate")
        if rate is not None:
            entry["_rates"][str(rate)] = entry["_rates"].get(str(rate), 0) + 1
        else:
            entry["taxable_add_missing_rate_count"] += 1
        base = metadata.get("base_receipt_key")
        if base:
            entry["_base_receipts"].add(str(base))

    # Finalise: convert working sets/dicts into stable, JSON-serialisable shapes.
    for entry in by_merchant.values():
        entry["taxable_add_rates"] = sorted(entry.pop("_rates").items())
        entry["taxable_add_base_receipts"] = sorted(entry.pop("_base_receipts"))
    return by_merchant


def _render_accepted_candidates(
    bundle_path: str,
    render_output_dir: str,
    merchant_name: str,
) -> list[str]:
    """Emit QA images per accepted candidate (Branch 2, stub until renderer lands)."""
    print(
        f"  [render] STUB: renderer not yet implemented "
        f"(branch feat/receipt-font-render). Skipping for {merchant_name!r}.",
        file=sys.stderr,
    )
    return []


# ---------------------------------------------------------------------------
# Per-merchant export
# ---------------------------------------------------------------------------

def export_merchant_receipts(
    merchant_name: str,
    table_name: str,
    output_dir: str,
    max_receipts: int,
    region: str,
) -> tuple[int, int, list[str]]:
    """Export up to max_receipts unique-image receipts for merchant_name.

    Returns ``(exported_count, failed_count, receipt_files)`` where
    ``receipt_files`` are the exact JSON paths exported/present for THIS run's
    image ids. The caller feeds those explicit files to the synthesis pipeline
    rather than the whole directory, so stale files left from a prior run with
    different parameters can't leak into the bundle.

    Uses ReceiptPlace (not the deprecated ReceiptMetadata) and paginates until
    it has ``max_receipts`` UNIQUE image ids (multiple receipts can share one
    image, so a single ``limit=max_receipts`` page can underfill after dedup).

    Per-image export failures are counted and returned (not raised): stale
    ReceiptPlace rows can point at image ids no longer in the table, which is
    expected and should not abort the merchant — but the count is surfaced in
    the report so an incomplete candidate pool is visible.
    """
    DynamoClient, export_image = _import_dynamo()
    client = DynamoClient(table_name, region=region)
    export_supports_region = _export_image_supports_region(export_image)

    # Paginate until max_receipts unique image ids, or the GSI is exhausted.
    seen_image_ids: set[str] = set()
    image_ids: list[str] = []
    last_key: dict | None = None
    while len(image_ids) < max_receipts:
        places, last_key = client.get_receipt_places_by_merchant(
            merchant_name, limit=max_receipts, last_evaluated_key=last_key
        )
        for place in places:
            iid = str(place.image_id)
            if iid not in seen_image_ids:
                seen_image_ids.add(iid)
                image_ids.append(iid)
                if len(image_ids) >= max_receipts:
                    break
        if not last_key:
            break

    image_ids = image_ids[:max_receipts]
    exported = 0
    failed = 0
    receipt_files: list[str] = []
    for image_id in image_ids:
        out_file = Path(output_dir) / f"{image_id}.json"
        if out_file.exists():
            exported += 1
            receipt_files.append(str(out_file))
            continue
        try:
            if export_supports_region:
                export_image(table_name, image_id, output_dir, region=region)
            else:
                # Underlying export_image is pinned to its default region; the
                # caller has already validated region == default, so this reads
                # the same table the ReceiptPlace lookup used.
                export_image(table_name, image_id, output_dir)
            exported += 1
            receipt_files.append(str(out_file))
        except Exception as exc:
            failed += 1
            print(
                f"  WARNING: export_image({image_id!r}) failed: {exc}",
                file=sys.stderr,
            )

    if failed:
        print(
            f"  {failed}/{len(image_ids)} exports failed for {merchant_name!r}",
            file=sys.stderr,
        )
    return exported, failed, receipt_files


# ---------------------------------------------------------------------------
# Phase 1: export each merchant's receipts
# ---------------------------------------------------------------------------

def export_phase(
    merchants: list[str],
    *,
    table_name: str,
    region: str,
    base_output_dir: str,
    max_receipts_per_merchant: int,
    research_dir: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Export each merchant's receipts into a per-merchant subdir.

    Returns ``(export_stats, receipt_files)`` where ``receipt_files`` are the
    explicit JSON paths exported for this run (across all merchants). Feeding
    explicit files — rather than whole dirs — keeps stale files from prior runs
    out of the bundle. A failed export for one merchant is recorded and skipped
    — it never aborts the others.
    """
    export_stats: list[dict[str, Any]] = []
    receipt_files: list[str] = []
    # A single image can hold receipts for two requested merchants and is then
    # exported into both merchant dirs under the same {image_id}.json name.
    # Feed each image payload to synthesis exactly once (dedupe by image id =
    # file stem) so shared images don't double-count source receipts/candidates.
    seen_image_stems: set[str] = set()
    for merchant in merchants:
        slug = _slug(merchant)
        receipt_dir = str(Path(base_output_dir) / "receipts" / slug)
        Path(receipt_dir).mkdir(parents=True, exist_ok=True)

        # Branch 1 hook: note an intelligence artifact when present (consumed
        # by the gate/config layer in a later milestone; recorded here for the
        # report so the run is auditable).
        research = _load_research_artifact(merchant, research_dir)
        if research:
            print(
                f"  [research] Loaded intelligence artifact for {merchant!r} "
                f"(confidence={research.get('confidence', '?')})"
            )

        print(f"[{merchant}] exporting receipts …")
        stat: dict[str, Any] = {
            "merchant_name": merchant,
            "research_artifact_used": research is not None,
            "receipt_dir": receipt_dir,
        }
        try:
            exported_count, failed_count, merchant_files = export_merchant_receipts(
                merchant,
                table_name=table_name,
                output_dir=receipt_dir,
                max_receipts=max_receipts_per_merchant,
                region=region,
            )
        except Exception as exc:
            stat.update(
                status="export_failed",
                exported_receipt_count=0,
                failed_image_count=0,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            export_stats.append(stat)
            print(f"  ERROR: export failed for {merchant!r}: {exc}", file=sys.stderr)
            continue

        stat["exported_receipt_count"] = exported_count
        stat["failed_image_count"] = failed_count
        # A whole-merchant query that returns rows but exports nothing is
        # "no_receipts"; partial per-image failures are surfaced separately and
        # are not treated as a merchant failure (expected for stale rows).
        stat["status"] = "exported" if exported_count else "no_receipts"
        export_stats.append(stat)
        suffix = f" ({failed_count} image(s) missing)" if failed_count else ""
        print(f"  exported {exported_count} image(s){suffix}.")
        for path in merchant_files:
            stem = Path(path).stem
            if stem not in seen_image_stems:
                seen_image_stems.add(stem)
                receipt_files.append(path)
    return export_stats, receipt_files


# ---------------------------------------------------------------------------
# Phase 3: per-merchant report from the single combined bundle
# ---------------------------------------------------------------------------

def _accepted_side_by_merchant(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-merchant accepted counts + operation mix from the accepted training set.

    Derived from ``synthetic_training_examples`` (the actual accepted set, capped
    only by the intended per-merchant/per-operation quantity limits, never by a
    merchant-list display cap). This is authoritative for the acceptance signal
    regardless of how many merchants appear, unlike ``candidate_mix.merchants``
    which the bundle truncates for display.
    """
    accepted: dict[str, dict[str, Any]] = {}
    for example in bundle.get("synthetic_training_examples") or []:
        merchant = str(example.get("merchant_name") or "unknown")
        operation = str((example.get("metadata") or {}).get("operation") or "unknown")
        entry = accepted.setdefault(
            merchant, {"accepted_count": 0, "accepted_operation_counts": {}}
        )
        entry["accepted_count"] += 1
        ops = entry["accepted_operation_counts"]
        ops[operation] = ops.get(operation, 0) + 1
    return accepted


def build_per_merchant_report(
    bundle: dict[str, Any],
    export_stats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per-merchant breakdown derived from the one combined bundle.

    Accepted-side metrics (accepted_count, accepted_operation_counts) and taxable
    adds are computed directly from the uncapped ``synthetic_training_examples``
    so they are correct for every merchant, even beyond the bundle's display cap
    on ``candidate_mix.merchants``. Rejection-side detail (candidate/rejected
    counts, rejection reasons) is read from ``candidate_mix.merchants`` when that
    merchant is within the (capped) list; ``rejection_detail_available`` flags
    when it is not, rather than silently reporting zero rejections. We join with
    export stats (which merchants were requested). Merchants present in the
    bundle but not requested are still listed — shared receipt images can carry
    other merchants' receipts.
    """
    candidate_mix = bundle.get("candidate_mix") or {}
    mix_merchants = candidate_mix.get("merchants") or []
    by_name: dict[str, dict[str, Any]] = {
        str(m.get("merchant_name")): m
        for m in mix_merchants
        if isinstance(m, dict) and m.get("merchant_name")
    }
    accepted_by_name = _accepted_side_by_merchant(bundle)
    taxable_by_merchant = _taxable_adds_by_merchant(bundle)
    export_by_name = {s["merchant_name"]: s for s in export_stats}

    # Requested merchants first (preserving request order), then any extra
    # merchants the bundle surfaced (from candidate_mix or the accepted set).
    ordered_names = [s["merchant_name"] for s in export_stats]
    for name in list(by_name) + list(accepted_by_name):
        if name not in export_by_name and name not in ordered_names:
            ordered_names.append(name)

    rows: list[dict[str, Any]] = []
    for name in ordered_names:
        mix = by_name.get(name) or {}
        accepted = accepted_by_name.get(name) or {}
        stat = export_by_name.get(name) or {}
        taxable = taxable_by_merchant.get(name) or {}
        rows.append(
            {
                "merchant_name": name,
                "requested": name in export_by_name,
                "export_status": stat.get("status", "not_requested"),
                "exported_receipt_count": stat.get("exported_receipt_count", 0),
                "failed_image_count": stat.get("failed_image_count", 0),
                "research_artifact_used": stat.get("research_artifact_used", False),
                "accepted_count": accepted.get("accepted_count", 0),
                "accepted_operation_counts": accepted.get(
                    "accepted_operation_counts"
                )
                or {},
                # Rejection detail only when this merchant is in the (capped)
                # candidate_mix list; flagged so a cap-miss isn't read as zero.
                "rejection_detail_available": name in by_name,
                "candidate_count": mix.get("candidate_count", 0),
                "rejected_count": mix.get("rejected_count", 0),
                "rejection_reasons": mix.get("rejection_reasons") or {},
                "taxable_add_count": taxable.get("taxable_add_count", 0),
                "taxable_add_missing_rate_count": taxable.get(
                    "taxable_add_missing_rate_count", 0
                ),
                "taxable_add_rates": taxable.get("taxable_add_rates", []),
                "taxable_add_base_receipts": taxable.get(
                    "taxable_add_base_receipts", []
                ),
                "export_error": stat.get("error"),
            }
        )
    return rows


def _effective_training_ready(bundle: dict[str, Any]) -> tuple[bool, list[str]]:
    """Training-readiness as the LayoutLM loader would judge it, plus reasons.

    The loader's final gate keys on the embedded synthesis_quality_report's
    ``training_ready`` (True/False decisive), falling back to that report's
    ``ready`` and then the bundle-level ``training_ready``/``ready``. The
    orchestrator mirrors that precedence so a bundle the loader would REJECT is
    never reported as ready / exited 0 here. Readiness is reported, never
    overridden — this only reflects the gate's own verdict.
    """
    report = bundle.get("synthesis_quality_report") or {}
    report_training_ready = report.get("training_ready")
    if report_training_ready is True:
        ready = True
    elif report_training_ready is False:
        ready = False
    elif report.get("ready") is False:
        ready = False
    else:
        bundle_training_ready = bundle.get("training_ready")
        if bundle_training_ready is True:
            ready = True
        elif bundle_training_ready is False:
            ready = False
        else:
            ready = bundle.get("ready") is True

    reasons = (
        report.get("training_ready_reasons")
        or report.get("bundle_reasons")
        or bundle.get("reasons")
        or []
    )
    return ready, [str(r) for r in reasons]


# ---------------------------------------------------------------------------
# Single run (one export → combined pipeline → report pass)
# ---------------------------------------------------------------------------

def run_single(
    *,
    merchants: list[str],
    table_name: str,
    region: str,
    base_output_dir: str,
    max_receipts_per_merchant: int,
    max_candidates: int,
    min_grounded_candidate_share: float,
    min_structure_similarity: float,
    max_per_merchant: int,
    max_per_merchant_operation: int,
    render: bool,
    research_dir: str | None,
    print_summary: bool = True,
) -> dict[str, Any]:
    """One export → combined pipeline → report pass. Returns the summary dict.

    The dict always carries a ``status`` field: ``ok`` when the pipeline ran and
    a bundle was read; ``no_receipts`` / ``pipeline_failed`` / ``bundle_read_failed``
    otherwise. Callers map status (and, for ``ok`` runs, export_failures and
    training_ready) to an exit code via _exit_code_from_summary.
    """
    # --- Phase 1: export every merchant's receipts ---
    export_stats, receipt_files = export_phase(
        merchants,
        table_name=table_name,
        region=region,
        base_output_dir=base_output_dir,
        max_receipts_per_merchant=max_receipts_per_merchant,
        research_dir=research_dir,
    )
    print()

    export_failures = [
        s["merchant_name"] for s in export_stats if s.get("status") == "export_failed"
    ]
    exported_merchant_count = sum(
        1 for s in export_stats if s.get("status") == "exported"
    )

    if not receipt_files:
        print(
            "ERROR: no receipts exported for any merchant; nothing to synthesise.",
            file=sys.stderr,
        )
        return {
            "status": "no_receipts",
            "requested_merchant_count": len(merchants),
            "exported_merchant_count": 0,
            "export_failures": export_failures,
            "export_stats": export_stats,
            "per_merchant": [],
        }

    # --- Phase 2: ONE combined local synthesis pipeline over all merchants ---
    # Mix-balance is a cross-merchant concentration signal, so the proven
    # backbone runs a single combined bundle rather than one bundle per merchant.
    # Explicit receipt_files (not whole dirs) ensure only THIS run's exports feed
    # the bundle, even if a reused dir holds stale files from a prior run.
    artifact_dir = str(Path(base_output_dir) / "artifacts" / "combined")
    bundle_path = str(Path(base_output_dir) / "bundles" / "combined.json")
    print(
        f"Running combined local synthesis pipeline over "
        f"{len(receipt_files)} receipt file(s) …"
    )
    replay = _import_replay()
    try:
        pipeline_result = replay.run_local_synthetic_pipeline(
            receipt_files=receipt_files,
            artifact_output_dir=artifact_dir,
            bundle_output=bundle_path,
            max_candidates=max_candidates,
            min_grounded_candidate_share=min_grounded_candidate_share,
            min_structure_similarity=min_structure_similarity,
            max_per_merchant=max_per_merchant,
            max_per_merchant_operation=max_per_merchant_operation,
        )
    except Exception as exc:
        print(f"ERROR: combined pipeline failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return {
            "status": "pipeline_failed",
            "requested_merchant_count": len(merchants),
            "exported_merchant_count": exported_merchant_count,
            "export_failures": export_failures,
            "error": str(exc),
            "per_merchant": [],
        }

    # --- Phase 3 (Branch 2): render hook over accepted candidates ---
    render_artifacts: list[str] = []
    if render:
        render_dir = str(Path(base_output_dir) / "renders")
        render_artifacts = _render_accepted_candidates(
            bundle_path, render_dir, "all-merchants"
        )

    # --- Load the written bundle once (the authoritative, untruncated source) ---
    # A read/parse failure here would silently zero out the acceptance signal,
    # so it fails the run rather than being swallowed.
    try:
        bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"ERROR: could not read written bundle {bundle_path!r}: {exc}",
            file=sys.stderr,
        )
        return {
            "status": "bundle_read_failed",
            "requested_merchant_count": len(merchants),
            "exported_merchant_count": exported_merchant_count,
            "export_failures": export_failures,
            "bundle_path": bundle_path,
            "error": str(exc),
            "per_merchant": [],
        }

    # --- Build report from the single bundle ---
    candidate_mix = bundle.get("candidate_mix") or {}
    mix_balance = candidate_mix.get("accepted_mix_balance") or {}
    per_merchant = build_per_merchant_report(bundle, export_stats)
    total_taxable_adds = sum(
        r.get("taxable_add_count", 0) or 0 for r in per_merchant
    )
    total_taxable_missing_rate = sum(
        r.get("taxable_add_missing_rate_count", 0) or 0 for r in per_merchant
    )
    total_failed_images = sum(
        s.get("failed_image_count", 0) or 0 for s in export_stats
    )
    # Readiness is the deterministic gate's verdict — reported, never overridden.
    # We mirror the loader's training-readiness precedence rather than only the
    # top-level bundle 'ready', so a bundle the loader would reject is not
    # reported ready / exited 0.
    training_ready, training_ready_reasons = _effective_training_ready(bundle)

    # Compact inline per-merchant status.
    for row in per_merchant:
        print(
            f"[{row['merchant_name']}] accepted={row['accepted_count']}  "
            f"taxable_adds={row['taxable_add_count']}  "
            f"rates={row['taxable_add_rates']}  export={row['export_status']}"
        )
    print()

    summary = {
        "status": "ok",
        "requested_merchant_count": len(merchants),
        "exported_merchant_count": exported_merchant_count,
        "export_failures": export_failures,
        "total_failed_images": total_failed_images,
        # bundle_ready reflects the loader's effective training-readiness gate.
        "bundle_ready": training_ready,
        "bundle_reasons": list(bundle.get("reasons") or []),
        "training_ready": training_ready,
        "training_ready_reasons": training_ready_reasons,
        "accepted_count": candidate_mix.get("accepted_count")
        or mix_balance.get("accepted_count", 0),
        "accepted_operation_counts": candidate_mix.get("accepted_operation_counts")
        or {},
        "mix_balance_risk": mix_balance.get("risk_level", "unknown"),
        "accepted_mix_balance": mix_balance,
        "total_taxable_adds": total_taxable_adds,
        "total_taxable_adds_missing_rate": total_taxable_missing_rate,
        "source_receipt_quality": bundle.get("source_receipt_quality") or {},
        "bundle_path": bundle_path,
        "render_artifacts": render_artifacts,
        "per_merchant": per_merchant,
    }

    if print_summary:
        print("=" * 60)
        print("CONSOLIDATED SUMMARY")
        print("=" * 60)
        _json_print(summary)

    return summary


def _exit_code_from_summary(summary: dict[str, Any]) -> int:
    """Map a run_single summary to an honest process exit code.

    2 = operational failure (export failed / no receipts / pipeline error)
    3 = ran cleanly but the deterministic gate marked the bundle not
        training-ready (the orchestrator does not override the gate)
    0 = ran cleanly and the bundle is training-ready
    """
    if summary.get("status") != "ok":
        return 2
    if summary.get("export_failures"):
        print(
            f"\nWARNING: {len(summary['export_failures'])} merchant(s) failed to "
            f"export: {summary['export_failures']}",
            file=sys.stderr,
        )
        return 2
    if not summary.get("training_ready"):
        print(
            "\nNOTE: bundle is not training-ready per the deterministic gate "
            f"(reasons: {summary.get('training_ready_reasons')}). Per-merchant "
            "evidence and the accepted mix above are still valid for review.",
            file=sys.stderr,
        )
        return 3
    return 0


# Mix-balance risk ordering (lower = safer); used by the coverage loop.
# "none" is what the producer emits when nothing is accepted yet (no
# concentration to worry about) — it is the safest rank, so an empty round can
# still escalate rather than being misread as a concentration hold.
_RISK_ORDER = {"none": 0, "low": 0, "medium": 1, "high": 2, "unknown": 3}


def run_coverage_loop(
    *,
    merchants: list[str],
    table_name: str,
    region: str,
    base_output_dir: str,
    coverage_target: int,
    max_balance_risk: str,
    max_coverage_rounds: int,
    coverage_receipt_step: int,
    max_receipts_cap: int,
    initial_max_receipts: int,
    max_candidates: int,
    min_grounded_candidate_share: float,
    min_structure_similarity: float,
    max_per_merchant: int,
    max_per_merchant_operation: int,
    render: bool,
    research_dir: str | None,
) -> int:
    """Agent-style coverage loop over the deterministic single-run pass.

    Each round runs run_single, then evaluates coverage: every requested
    merchant should reach ``coverage_target`` accepted synthetic rows while the
    cross-merchant mix-balance risk stays at or below ``max_balance_risk``. If
    some merchants are under target and risk is acceptable, it escalates the
    per-merchant receipt budget (more source data) and retries — until targets
    are met, the budget cap / round cap is hit, a round makes no progress, or
    risk exceeds the threshold (a concentration hold that more data won't fix).

    The loop never relaxes a gate; it only decides what to run next. Coverage
    being unmet is reported honestly, not papered over.
    """
    allowed_risk_rank = _RISK_ORDER.get(max_balance_risk, 0)
    # Honor the budget cap from the very first round (the initial budget can be
    # larger than the cap when the caller sets a big --max-receipts-per-merchant).
    max_receipts = min(initial_max_receipts, max_receipts_cap)
    rounds: list[dict[str, Any]] = []
    final_summary: dict[str, Any] = {}
    outcome = "rounds_exhausted"
    prev_under_total: int | None = None

    for round_idx in range(1, max_coverage_rounds + 1):
        print("#" * 60)
        print(
            f"COVERAGE ROUND {round_idx}/{max_coverage_rounds} — "
            f"max_receipts_per_merchant={max_receipts}, target={coverage_target}/merchant"
        )
        print("#" * 60)
        summary = run_single(
            merchants=merchants,
            table_name=table_name,
            region=region,
            base_output_dir=base_output_dir,
            max_receipts_per_merchant=max_receipts,
            max_candidates=max_candidates,
            min_grounded_candidate_share=min_grounded_candidate_share,
            min_structure_similarity=min_structure_similarity,
            max_per_merchant=max_per_merchant,
            max_per_merchant_operation=max_per_merchant_operation,
            render=render,
            research_dir=research_dir,
            print_summary=False,
        )
        final_summary = summary

        if summary.get("status") != "ok":
            outcome = f"operational_failure:{summary.get('status')}"
            rounds.append(
                {
                    "round": round_idx,
                    "max_receipts_per_merchant": max_receipts,
                    "status": summary.get("status"),
                }
            )
            break

        # A whole-merchant export failure is an operational failure, same as in
        # single-run mode (exit 2) — surface it rather than letting an unmet
        # loop report the softer "not satisfied" (3).
        if summary.get("export_failures"):
            outcome = "operational_failure:export_failed"
            rounds.append(
                {
                    "round": round_idx,
                    "max_receipts_per_merchant": max_receipts,
                    "export_failures": summary.get("export_failures"),
                }
            )
            break

        accepted_by_merchant = {
            r["merchant_name"]: r.get("accepted_count", 0)
            for r in summary.get("per_merchant", [])
        }
        under = {
            m: accepted_by_merchant.get(m, 0)
            for m in merchants
            if accepted_by_merchant.get(m, 0) < coverage_target
        }
        risk = summary.get("mix_balance_risk", "unknown")
        risk_rank = _RISK_ORDER.get(risk, 3)
        under_total = sum(coverage_target - v for v in under.values())

        round_record = {
            "round": round_idx,
            "max_receipts_per_merchant": max_receipts,
            "accepted_by_requested_merchant": {
                m: accepted_by_merchant.get(m, 0) for m in merchants
            },
            "under_target": under,
            "mix_balance_risk": risk,
            "export_failures": summary.get("export_failures", []),
        }
        rounds.append(round_record)
        print(
            f"  → round {round_idx}: under_target={under} risk={risk} "
            f"(allowed≤{max_balance_risk})"
        )

        if risk_rank > allowed_risk_rank:
            # A concentration hold: escalating uniformly would add more of the
            # dominant merchant and not fix balance. Stop and report honestly.
            outcome = "mix_balance_hold"
            break
        if not under:
            outcome = "coverage_met"
            break
        if prev_under_total is not None and under_total >= prev_under_total:
            # No improvement vs the previous round — more budget isn't helping
            # (e.g. source receipts exhausted). Stop rather than spin.
            outcome = "no_progress"
            break
        prev_under_total = under_total
        if max_receipts >= max_receipts_cap:
            outcome = "receipts_cap_reached"
            break
        max_receipts = min(max_receipts + coverage_receipt_step, max_receipts_cap)

    coverage_report = {
        "mode": "coverage_loop",
        "coverage_target": coverage_target,
        "max_balance_risk": max_balance_risk,
        "outcome": outcome,
        "rounds_run": len(rounds),
        "rounds": rounds,
        "final_summary": final_summary,
    }
    print("=" * 60)
    print("COVERAGE LOOP REPORT")
    print("=" * 60)
    _json_print(coverage_report)

    # Exit code: 0 only when coverage met AND the gate says training-ready.
    if outcome == "coverage_met":
        return _exit_code_from_summary(final_summary)
    if outcome.startswith("operational_failure"):
        return 2
    # Coverage genuinely not satisfied (hold / no progress / exhausted): 3.
    print(
        f"\nNOTE: coverage not satisfied (outcome={outcome}). See the coverage "
        "report above for per-round evidence.",
        file=sys.stderr,
    )
    return 3


# ---------------------------------------------------------------------------
# Main orchestrator / CLI dispatch
# ---------------------------------------------------------------------------

def run_pipeline(args: argparse.Namespace) -> int:
    table_name = args.table_name or os.environ.get("DYNAMO_TABLE_NAME", "")
    if not table_name:
        print(
            "ERROR: --table-name or DYNAMO_TABLE_NAME env var required.",
            file=sys.stderr,
        )
        return 1

    region = args.region or os.environ.get("AWS_REGION", "us-east-1")
    merchants = args.merchants or DEFAULT_MERCHANTS
    base_output_dir = args.output_dir

    # Fail fast on a region the export step cannot honor: if the installed
    # export_image does not accept a region, it builds its client at
    # _EXPORT_IMAGE_DEFAULT_REGION, so a different --region would silently read a
    # different table from the ReceiptPlace lookup and look like export failures.
    _, export_image = _import_dynamo()
    if (
        not _export_image_supports_region(export_image)
        and region != _EXPORT_IMAGE_DEFAULT_REGION
    ):
        print(
            f"ERROR: the installed export_image is pinned to region "
            f"{_EXPORT_IMAGE_DEFAULT_REGION!r} and cannot export from "
            f"--region={region!r}. Re-run with the default region or upgrade "
            f"export_image to accept a region.",
            file=sys.stderr,
        )
        return 1

    mode = "coverage loop" if args.coverage_target > 0 else "single run"
    print(f"Synthesis pipeline — {len(merchants)} merchant(s) [{mode}]")
    print(f"  table={table_name}  region={region}")
    print(f"  output_dir={base_output_dir}")
    if args.research_dir:
        print(f"  [Branch 1] research_dir={args.research_dir}")
    if args.render:
        print("  [Branch 2] render=enabled (STUB)")
    print()

    if args.coverage_target > 0:
        return run_coverage_loop(
            merchants=merchants,
            table_name=table_name,
            region=region,
            base_output_dir=base_output_dir,
            coverage_target=args.coverage_target,
            max_balance_risk=args.max_balance_risk,
            max_coverage_rounds=args.max_coverage_rounds,
            coverage_receipt_step=args.coverage_receipt_step,
            max_receipts_cap=args.max_receipts_cap,
            initial_max_receipts=args.max_receipts_per_merchant,
            max_candidates=args.max_candidates,
            min_grounded_candidate_share=args.min_grounded_candidate_share,
            min_structure_similarity=args.min_structure_similarity,
            max_per_merchant=args.max_per_merchant,
            max_per_merchant_operation=args.max_per_merchant_operation,
            render=args.render,
            research_dir=args.research_dir,
        )

    summary = run_single(
        merchants=merchants,
        table_name=table_name,
        region=region,
        base_output_dir=base_output_dir,
        max_receipts_per_merchant=args.max_receipts_per_merchant,
        max_candidates=args.max_candidates,
        min_grounded_candidate_share=args.min_grounded_candidate_share,
        min_structure_similarity=args.min_structure_similarity,
        max_per_merchant=args.max_per_merchant,
        max_per_merchant_operation=args.max_per_merchant_operation,
        render=args.render,
        research_dir=args.research_dir,
    )
    return _exit_code_from_summary(summary)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Synthetic receipt pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--merchants",
        nargs="+",
        default=None,
        metavar="MERCHANT",
        help=(
            "Merchant names to process "
            f"(default: {DEFAULT_MERCHANTS})"
        ),
    )
    p.add_argument(
        "--table-name",
        default=None,
        help="DynamoDB table name (or set DYNAMO_TABLE_NAME env var)",
    )
    p.add_argument(
        "--region",
        default=None,
        help="AWS region (or set AWS_REGION; default us-east-1)",
    )
    p.add_argument(
        "--output-dir",
        default=".tmp/synthesis-pipeline",
        help="Base output directory for receipts, artifacts, bundles",
    )
    p.add_argument(
        "--max-receipts-per-merchant",
        type=int,
        default=50,
        help="Max receipt images to export per merchant",
    )
    p.add_argument(
        "--max-candidates",
        type=int,
        default=80,
        help="Max synthesis candidates to generate (--max-candidates in local-pipeline)",
    )
    p.add_argument(
        "--min-grounded-candidate-share",
        type=float,
        default=0.0,
        help="Min fraction of candidates that must be grounded (0.0 = off)",
    )
    p.add_argument(
        "--max-per-merchant",
        type=int,
        default=100,
        help="Max accepted candidates per merchant in the bundle",
    )
    p.add_argument(
        "--max-per-merchant-operation",
        type=int,
        default=60,
        help="Max accepted candidates per merchant-operation pair in the bundle",
    )
    p.add_argument(
        "--min-structure-similarity",
        type=float,
        default=0.6,
        help="Min structure similarity score for high-fidelity gate",
    )
    # Branch 1 hook
    p.add_argument(
        "--research-dir",
        default=None,
        metavar="DIR",
        help=(
            "[Branch 1] Path to merchant_intelligence/ dir. "
            "Reads <slug>.json when present; falls back to hardcoded config."
        ),
    )
    # Branch 2 hook
    p.add_argument(
        "--render",
        action="store_true",
        default=False,
        help="[Branch 2] Emit QA render images per accepted candidate (STUB)",
    )
    # Coverage-loop mode (agent-style: retry + escalate until targets met)
    cov = p.add_argument_group("coverage loop (set --coverage-target > 0 to enable)")
    cov.add_argument(
        "--coverage-target",
        type=int,
        default=0,
        help=(
            "Min accepted synthetic rows required per requested merchant. "
            ">0 enables the coverage loop (escalate receipts + retry until met)."
        ),
    )
    cov.add_argument(
        "--max-balance-risk",
        choices=["low", "medium", "high"],
        default="low",
        help="Max acceptable cross-merchant mix-balance risk (coverage loop)",
    )
    cov.add_argument(
        "--max-coverage-rounds",
        type=int,
        default=4,
        help="Max coverage-loop rounds before stopping",
    )
    cov.add_argument(
        "--coverage-receipt-step",
        type=int,
        default=25,
        help="Per-merchant receipt budget increase between coverage rounds",
    )
    cov.add_argument(
        "--max-receipts-cap",
        type=int,
        default=200,
        help="Upper bound on per-merchant receipt budget during the coverage loop",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
