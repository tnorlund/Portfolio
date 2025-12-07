#!/usr/bin/env python3
"""
Render the LLM combination prompt for a given image/target receipt.

Usage:
  python dev.render_combination_prompt.py --image-id <image> [--target-receipt-id <id>] [--table-name ...] [--out prompt.txt]

If --target-receipt-id is omitted, the script picks the first receipt without metadata (or falls back to the first receipt).
Requires env (Pulumi) or explicit table env var (DYNAMODB_TABLE_NAME), or --table-name.
"""

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Tuple

# ensure local receipt_upload imports work before dependent modules load
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "receipt_upload"))
sys.path.insert(0, str(REPO_ROOT / "receipt_upload" / "receipt_upload"))

from receipt_agent.agent.combination_selector import (
    _warp_point,  # type: ignore
)
from receipt_agent.agent.combination_selector import ReceiptCombinationSelector
from receipt_agent.utils.receipt_coordinates import (
    get_receipt_to_image_transform,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.entities import Receipt
from receipt_upload.cluster import reorder_box_points  # noqa: E402
from receipt_upload.geometry import box_points, min_area_rect  # noqa: E402
from receipt_upload.geometry.transformations import (  # noqa: E402
    find_perspective_coeffs as _find_perspective_coeffs,
)


def pick_target_without_metadata(client: DynamoClient, image_id: str) -> int:
    receipts: List[Receipt] = client.get_receipts_from_image(image_id)
    if not receipts:
        raise SystemExit("No receipts found for that image.")
    for r in receipts:
        try:
            meta = client.get_receipt_metadata(image_id, r.receipt_id)
            if not meta or not (meta.merchant_name or meta.place_id):
                return r.receipt_id
        except Exception:
            return r.receipt_id
    return receipts[0].receipt_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Render combination prompt")
    parser.add_argument("--image-id", required=True)
    parser.add_argument(
        "--target-receipt-id",
        type=int,
        help="Anchor receipt; if omitted, pick one without metadata",
    )
    parser.add_argument(
        "--table-name", help="DynamoDB table name (overrides env)"
    )
    parser.add_argument("--out", type=Path, default=Path("prompt.txt"))
    args = parser.parse_args()

    # Hydrate env to pick up DYNAMODB_TABLE_NAME if available
    try:
        stack_env = load_env()
        _ = load_secrets()
        table = stack_env.get("dynamodb_table_name") or stack_env.get(
            "receipts_table_name"
        )
        if table:
            os.environ.setdefault("DYNAMODB_TABLE_NAME", table)
    except Exception:
        pass

    table_name = args.table_name or os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise SystemExit(
            "DYNAMODB_TABLE_NAME not set; set env or use --table-name"
        )

    client = DynamoClient(table_name)
    target_id = args.target_receipt_id or pick_target_without_metadata(
        client, args.image_id
    )

    selector = ReceiptCombinationSelector(client)
    candidates = selector.build_candidates(args.image_id, target_id)

    # Re-render each candidate using warped combined receipt lines (min-area rect)
    try:
        image = client.get_image(args.image_id)
        image_w, image_h = image.width, image.height

        def collect_pts(receipt, lines):
            coeffs, rw, rh = get_receipt_to_image_transform(
                receipt, image_w, image_h
            )
            pts = []
            for ln in lines:
                for corner in [
                    ln.top_left,
                    ln.top_right,
                    ln.bottom_right,
                    ln.bottom_left,
                ]:
                    rx = corner["x"] * rw
                    ry = corner["y"] * rh
                    pts.append(_warp_point(coeffs, rx, ry))
            return pts

        def warp_lines_collect(receipt, lines, src_to_dst):
            coeffs, rw, rh = get_receipt_to_image_transform(
                receipt, image_w, image_h
            )
            collected = []
            a, b, c, d, e, f, g, h = src_to_dst
            for ln in lines:
                corners_img = []
                for corner in [
                    ln.top_left,
                    ln.top_right,
                    ln.bottom_right,
                    ln.bottom_left,
                ]:
                    rx = corner["x"] * rw
                    ry = corner["y"] * rh
                    corners_img.append(_warp_point(coeffs, rx, ry))
                corners_warped = []
                for x, y in corners_img:
                    den = g * x + h * y + 1.0
                    if abs(den) < 1e-12:
                        corners_warped.append((x, y))
                    else:
                        corners_warped.append(
                            (
                                (a * x + b * y + c) / den,
                                (d * x + e * y + f) / den,
                            )
                        )
                cx = sum(p[0] for p in corners_warped) / 4.0
                cy = sum(p[1] for p in corners_warped) / 4.0
                collected.append((cy, cx, ln.text, ln.line_id, corners_warped))
            return collected

        def render_grouped(all_warped):
            all_warped.sort(key=lambda t: (t[0], t[1]))
            grouped = []
            for cy, cx, text, line_id, pts in all_warped:
                if grouped:
                    prev = grouped[-1]
                    prev_pts = prev[-1][4]
                    prev_top = min(p[1] for p in prev_pts)
                    prev_bot = max(p[1] for p in prev_pts)
                    prev_h = max(prev_bot - prev_top, 1.0)
                    curr_top = min(p[1] for p in pts)
                    curr_bot = max(p[1] for p in pts)
                    curr_h = max(curr_bot - curr_top, 1.0)
                    tol = max(max(prev_h, curr_h) * 0.5, 4.0)
                    if (prev_top - tol) <= cy <= (prev_bot + tol):
                        grouped[-1].append((cy, cx, text, line_id, pts))
                        continue
                grouped.append([(cy, cx, text, line_id, pts)])

            rendered = []
            new_id = 1
            for grp in grouped:
                grp_sorted = sorted(grp, key=lambda t: t[1])
                texts = []
                for _, _, txt, lid, _pts in grp_sorted:
                    texts.append(txt)
                rendered.append(f"{new_id}: {' '.join(texts)}")
                new_id += 1
            return "\n".join(rendered)

        new_candidates = []
        for cand in candidates:
            a, b = cand["combo"]
            receipt_a = client.get_receipt(args.image_id, a)
            receipt_b = client.get_receipt(args.image_id, b)
            lines_a = client.list_receipt_lines_from_receipt(args.image_id, a)
            lines_b = client.list_receipt_lines_from_receipt(args.image_id, b)
            pts = collect_pts(receipt_a, lines_a) + collect_pts(
                receipt_b, lines_b
            )
            if not pts:
                new_candidates.append(cand)
                continue
            (cx, cy), (rw, rh), angle = min_area_rect(pts)
            src_corners = reorder_box_points(
                box_points((cx, cy), (rw, rh), angle)
            )
            dst = [
                (0, 0),
                (rw - 1, 0),
                (rw - 1, rh - 1),
                (0, rh - 1),
            ]
            src_to_dst = _find_perspective_coeffs(src_corners, dst)
            warped_a = warp_lines_collect(receipt_a, lines_a, src_to_dst)
            warped_b = warp_lines_collect(receipt_b, lines_b, src_to_dst)
            text_merged = render_grouped(warped_a + warped_b)
            cand = {
                **cand,
                "text_combined": text_merged,
                "text_target": "",
                "text_other": "",
            }
            new_candidates.append(cand)
        candidates = new_candidates
    except Exception:
        traceback.print_exc()

    if not candidates:
        raise SystemExit("No candidates found for that image/target")

    prompt = selector._build_prompt(candidates)  # noqa: SLF001 - dev helper
    args.out.write_text(prompt, encoding="utf-8")
    print(f"Wrote prompt to {args.out}")

    # Also echo to stdout (truncated)
    preview = "\n".join(prompt.splitlines()[:80])
    print("\n=== Metadata ===")
    print(f"image_id: {args.image_id}")
    print(f"target_receipt_id: {target_id}")
    print(f"candidate_pairs: {[c['combo'] for c in candidates]}")
    print(f"prompt_length: {len(prompt)}")
    print("\n=== Prompt Preview (first ~80 lines) ===\n")
    print(preview)


if __name__ == "__main__":
    main()
