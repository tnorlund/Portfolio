"""
Batch orchestrator: take OCR-outlier candidates and queue regional
re-OCR jobs for each affected receipt.

Pipeline:
    detect (ocr_outlier_prototype)
      → group by (image_id, receipt_id)
      → for each group: trigger-reocr Lambda with padded line_ids
      → optionally run the Swift worker
      → re-query to verify text changed

Dry-run by default. Pass --apply to actually invoke the Lambda.

Usage:
    PORTFOLIO_ENV=dev python scripts/ocr_outlier_batch.py \\
        --min-popular-count 10 \\
        --max-images 5 \\
        --apply

After --apply, run the Swift worker once to drain the queue:
    ./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr \\
        --env dev --log-level info

Then re-run this script with --verify and the saved job-id file to
diff old vs new text for each candidate.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Make sibling import work when invoked from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ocr_outlier_prototype import (  # noqa: E402
    _levenshtein,
    _load_clients,
    _paginate_all_validated_words,
    build_trigram_model,
    find_pair_outliers,
    trigram_logprob,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


def _filter_dry_run(
    candidates: list[dict[str, Any]],
    *,
    min_count: int,
    max_edit: int,
    max_length_diff: int,
) -> list[dict[str, Any]]:
    """Apply the dry-run-corrections thresholds (same gates as prototype)."""
    kept: list[dict[str, Any]] = []
    for c in candidates:
        suspect = (c["suspect_text"] or "").upper()
        suggested = (c["suggested_text"] or "").upper()
        if (c["suggested_count"] or 0) < min_count:
            continue
        if _levenshtein(suspect, suggested) > max_edit:
            continue
        if abs(len(suspect) - len(suggested)) > max_length_diff:
            continue
        kept.append(c)
    return kept


def group_candidates(
    candidates: list[dict[str, Any]],
    *,
    pad: int = 1,
) -> list[dict[str, Any]]:
    """
    Group candidates by (image_id, receipt_id) and union their line_ids.

    pad=N expands each line_id to [lid-N .. lid+N] to cover Chroma vs
    DynamoDB indexing slop. The trigger-reocr Lambda already adds a
    full neighbor line of vertical padding on top of this, so pad=1
    is usually enough.
    """
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    for c in candidates:
        image_id = c.get("image_id")
        receipt_id = c.get("receipt_id")
        line_id = c.get("line_id")
        if not image_id or receipt_id is None or line_id is None:
            continue
        key = (str(image_id), int(receipt_id))
        bucket = groups.setdefault(
            key,
            {
                "image_id": str(image_id),
                "receipt_id": int(receipt_id),
                "line_ids": set(),
                "candidates": [],
            },
        )
        bucket["candidates"].append(c)
        for lid in range(int(line_id) - pad, int(line_id) + pad + 1):
            if lid >= 0:
                bucket["line_ids"].add(lid)

    out = []
    for g in groups.values():
        out.append({
            "image_id": g["image_id"],
            "receipt_id": g["receipt_id"],
            "line_ids": sorted(g["line_ids"]),
            "candidates": g["candidates"],
        })
    out.sort(key=lambda g: -len(g["candidates"]))
    return out


def _invoke_trigger_reocr(
    lambda_client,
    function_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Synchronously invoke the trigger-reocr Lambda."""
    resp = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["Payload"].read().decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {"raw": body, "status_code": resp.get("StatusCode")}


def _compute_region_for_lines(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    line_ids: list[int],
) -> dict[str, Any]:
    """Compute the full-width re-OCR region from a set of line_ids.

    Mirrors the logic in the receipt_mcp_server.trigger_reocr_impl
    (worktree version), so the worker sees the same crop the MCP tool
    would produce.
    """
    from receipt_upload.geometry.transformations import find_perspective_coeffs

    details = dynamo_client.get_receipt_details(image_id, receipt_id)
    all_words = details.words

    line_extents: dict[int, tuple[float, float]] = {}
    for w in all_words:
        lid = w.line_id
        y_top = w.bounding_box["y"]
        y_bot = y_top + w.bounding_box["height"]
        if lid not in line_extents:
            line_extents[lid] = (y_top, y_bot)
        else:
            line_extents[lid] = (
                min(line_extents[lid][0], y_top),
                max(line_extents[lid][1], y_bot),
            )

    target_set = set(line_ids)
    found = sorted(target_set & set(line_extents.keys()))
    if not found:
        return {
            "error": f"No words found on line_ids {line_ids}",
            "available_line_ids": sorted(line_extents.keys())[:20],
        }

    all_line_ids = sorted(line_extents.keys())
    min_target, max_target = min(found), max(found)
    lines_above = [lid for lid in all_line_ids if lid < min_target]
    lines_below = [lid for lid in all_line_ids if lid > max_target]

    padded_y = (
        line_extents[lines_above[-1]][0] if lines_above else 0.0
    )
    padded_top = (
        line_extents[lines_below[0]][1] if lines_below else 1.0
    )

    receipt = details.receipt
    image = dynamo_client.get_image(image_id)

    src_points = [
        (receipt.top_left["x"] * image.width,
         (1.0 - receipt.top_left["y"]) * image.height),
        (receipt.top_right["x"] * image.width,
         (1.0 - receipt.top_right["y"]) * image.height),
        (receipt.bottom_right["x"] * image.width,
         (1.0 - receipt.bottom_right["y"]) * image.height),
        (receipt.bottom_left["x"] * image.width,
         (1.0 - receipt.bottom_left["y"]) * image.height),
    ]
    dst_points = [
        (0.0, 0.0),
        (float(receipt.width - 1), 0.0),
        (float(receipt.width - 1), float(receipt.height - 1)),
        (0.0, float(receipt.height - 1)),
    ]
    coeffs = find_perspective_coeffs(src_points, dst_points)

    def _pt(rx: float, ry: float) -> tuple[float, float]:
        x_rct = rx * (receipt.width - 1)
        y_rct = (1.0 - ry) * (receipt.height - 1)
        a, b, c, d, e, f, g, h = coeffs
        denom = 1.0 + g * x_rct + h * y_rct
        if abs(denom) < 1e-12:
            return 0.5, 0.5
        x_img = (a * x_rct + b * y_rct + c) / denom
        y_img = (d * x_rct + e * y_rct + f) / denom
        ix = x_img / image.width
        iy = 1.0 - (y_img / image.height)
        return max(0.0, min(1.0, ix)), max(0.0, min(1.0, iy))

    corners = [
        _pt(0.0, padded_y),
        _pt(1.0, padded_y),
        _pt(0.0, padded_top),
        _pt(1.0, padded_top),
    ]
    img_min_x = max(0.0, min(c[0] for c in corners))
    img_min_y = max(0.0, min(c[1] for c in corners))
    img_max_x = min(1.0, max(c[0] for c in corners))
    img_max_y = min(1.0, max(c[1] for c in corners))

    if img_max_x <= img_min_x or img_max_y <= img_min_y:
        return {"error": "Computed region is empty after transform"}

    return {
        "region": {
            "x": round(img_min_x, 6),
            "y": round(img_min_y, 6),
            "width": round(img_max_x - img_min_x, 6),
            "height": round(img_max_y - img_min_y, 6),
        },
        "lines_included": found,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-popular-count", type=int, default=10)
    parser.add_argument("--cutoff", type=float, default=0.6)
    parser.add_argument("--max-edit", type=int, default=1)
    parser.add_argument("--max-length-diff", type=int, default=2)
    parser.add_argument(
        "--max-images",
        type=int,
        default=5,
        help="Cap on receipts to re-OCR in one batch. Default 5 for safety.",
    )
    parser.add_argument(
        "--pad",
        type=int,
        default=1,
        help="Pad each candidate's line_id by ±N to cover indexing slop.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually invoke the trigger-reocr Lambda. Default: dry-run.",
    )
    parser.add_argument(
        "--job-log",
        type=str,
        default="ocr_outlier_jobs.json",
        help="Where to write the list of (image_id, receipt_id, job_id) records.",
    )
    args = parser.parse_args()

    env = os.environ.get("PORTFOLIO_ENV", "dev")
    function_name = f"trigger-reocr-{env}-trigger-reocr"
    logger.info("Target env: %s (Lambda: %s)", env, function_name)

    dynamo, chroma = _load_clients()
    words_col = chroma.get_collection("words")

    all_metas = _paginate_all_validated_words(words_col)
    logger.info("Loaded %d validated words from Chroma", len(all_metas))

    bigram, trigram, vocab = build_trigram_model(
        [m.get("text") or "" for m in all_metas]
    )

    candidates = find_pair_outliers(
        all_metas,
        min_popular_count=args.min_popular_count,
        cutoff=args.cutoff,
    )
    candidates = _filter_dry_run(
        candidates,
        min_count=args.min_popular_count,
        max_edit=args.max_edit,
        max_length_diff=args.max_length_diff,
    )
    logger.info(
        "Filtered to %d candidates (count>=%d, edit<=%d, len-diff<=%d)",
        len(candidates),
        args.min_popular_count,
        args.max_edit,
        args.max_length_diff,
    )

    groups = group_candidates(candidates, pad=args.pad)
    logger.info("Grouped into %d (image, receipt) bundles", len(groups))

    batch = groups[: args.max_images]
    logger.info(
        "Will process top %d bundles (capped by --max-images)", len(batch)
    )

    print()
    print(
        f"{'#':>3} {'IMAGE_ID':<36} {'R':>3} {'#WORDS':>6} {'LINE_IDS':<60}"
    )
    print("-" * 120)
    for i, g in enumerate(batch, 1):
        lids = g["line_ids"]
        lids_str = ",".join(str(x) for x in lids[:12]) + (
            f" (+{len(lids) - 12} more)" if len(lids) > 12 else ""
        )
        print(
            f"{i:>3} {g['image_id']:<36} {g['receipt_id']:>3} "
            f"{len(g['candidates']):>6}  {lids_str:<60}"
        )
        for c in g["candidates"][:5]:
            sc = trigram_logprob(c["suspect_text"], bigram, trigram, vocab)
            print(
                f"      └─ '{c['suspect_text']}' → '{c['suggested_text']}'"
                f" (pop={c['suggested_count']}, ngram={sc:.2f})"
            )
        if len(g["candidates"]) > 5:
            print(f"      └─ ... +{len(g['candidates']) - 5} more")
    print()

    if not args.apply:
        print("Dry-run only. Re-run with --apply to invoke the Lambda.")
        return 0

    import boto3

    lambda_client = boto3.client("lambda", region_name="us-east-1")

    fired: list[dict[str, Any]] = []
    for i, g in enumerate(batch, 1):
        region_result = _compute_region_for_lines(
            dynamo, g["image_id"], g["receipt_id"], g["line_ids"]
        )
        if "error" in region_result:
            logger.warning(
                "[%d] %s receipt=%d skipped: %s",
                i,
                g["image_id"],
                g["receipt_id"],
                region_result["error"],
            )
            continue

        payload = {
            "image_id": g["image_id"],
            "receipt_id": g["receipt_id"],
            "reocr_region": region_result["region"],
            "reocr_reason": "ocr_outlier_batch",
        }
        resp = _invoke_trigger_reocr(lambda_client, function_name, payload)
        job_id = resp.get("job_id")
        logger.info(
            "[%d] %s receipt=%d lines=%s → job=%s",
            i,
            g["image_id"],
            g["receipt_id"],
            region_result["lines_included"],
            job_id or resp,
        )
        fired.append({
            "image_id": g["image_id"],
            "receipt_id": g["receipt_id"],
            "line_ids": g["line_ids"],
            "lines_included": region_result["lines_included"],
            "region": region_result["region"],
            "job_id": job_id,
            "candidates": [
                {
                    "suspect_text": c["suspect_text"],
                    "suggested_text": c["suggested_text"],
                    "suggested_count": c["suggested_count"],
                    "line_id": c["line_id"],
                    "word_id": c["word_id"],
                }
                for c in g["candidates"]
            ],
            "lambda_response": resp,
        })

    log_path = Path(args.job_log)
    log_path.write_text(json.dumps(fired, indent=2))
    logger.info(
        "Fired %d trigger-reocr jobs; recorded to %s",
        len(fired),
        log_path,
    )
    print(
        "\nNext: run the Swift worker to drain the queue:\n"
        f"  ./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr "
        f"--env {env} --log-level info"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
