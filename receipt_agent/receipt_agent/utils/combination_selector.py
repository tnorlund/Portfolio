"""
LLM-assisted receipt combination selector.

Given a target receipt that lacks metadata, this helper builds candidate
combinations with other receipts from the same image, renders their text in
image order (top-to-bottom, left-to-right), and asks an LLM to choose the best
combination or NONE.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from langchain_ollama import ChatOllama

# Optional LangSmith tracing (graceful if not installed)
try:
    from langsmith.run_tree import RunTree
except ImportError:  # pragma: no cover - optional dependency
    RunTree = None  # type: ignore

from receipt_agent.config.settings import get_settings
from receipt_agent.utils.receipt_coordinates import (
    get_receipt_to_image_transform,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.entities import Receipt, ReceiptLine

# Make receipt_upload available for geometry helpers (min-area rect, reorder)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "receipt_upload"))
sys.path.insert(0, os.path.join(REPO_ROOT, "receipt_upload", "receipt_upload"))

from receipt_upload.cluster import reorder_box_points  # noqa: E402
from receipt_upload.geometry import box_points, min_area_rect  # noqa: E402
from receipt_upload.geometry.transformations import (  # noqa: E402
    find_perspective_coeffs,
    invert_warp,
)


def _warp_point(coeffs: Sequence[float], x: float, y: float) -> Tuple[float, float]:
    """
    Apply perspective transform (receipt space -> image space).
    coeffs: [a, b, c, d, e, f, g, h]
    """
    a, b, c, d, e, f, g, h = coeffs
    den = g * x + h * y + 1.0
    if abs(den) < 1e-12:
        return (x, y)
    return ((a * x + b * y + c) / den, (d * x + e * y + f) / den)


def _line_centroid_image(
    receipt: Receipt, line: ReceiptLine, image_width: int, image_height: int
) -> Tuple[float, float]:
    """Compute line centroid in image space using the receipt transform."""
    coeffs, receipt_w, receipt_h = receipt.get_transform_to_image(
        image_width, image_height
    )
    # Lines store corners normalized to receipt space, y in OCR space (bottom=0).
    pts = [
        line.top_left,
        line.top_right,
        line.bottom_right,
        line.bottom_left,
    ]
    px_pts = []
    for pt in pts:
        rx = pt["x"] * receipt_w
        ry = pt["y"] * receipt_h
        x_img, y_img = _warp_point(coeffs, rx, ry)
        px_pts.append((x_img, y_img))
    cx = sum(p[0] for p in px_pts) / 4.0
    cy = sum(p[1] for p in px_pts) / 4.0
    return cx, cy


def _sort_lines_ocr_space(lines: Iterable[ReceiptLine]) -> List[ReceiptLine]:
    """
    Sort lines in OCR space (y=0 at bottom) without warping to image space.
    Top-to-bottom: larger y first, then left-to-right: smaller x first.
    """
    with_keys = []
    for ln in lines:
        # Use top_left corner (already normalized 0-1 in OCR space)
        y = ln.top_left["y"]
        x = ln.top_left["x"]
        with_keys.append((-y, x, ln))  # negate y to sort descending by y
    with_keys.sort(key=lambda t: (t[0], t[1]))
    return [ln for _, _, ln in with_keys]


def _warp_lines_to_common_space(
    receipt: Receipt,
    lines: Sequence[ReceiptLine],
    image_width: int,
    image_height: int,
    src_to_dst: Sequence[float],
) -> List[Tuple[float, float, float, float, str]]:
    """Warp lines to common space; return (top, bottom, cx, cy, text)."""
    coeffs, receipt_w, receipt_h = get_receipt_to_image_transform(
        receipt, image_width, image_height
    )
    a, b, c, d, e, f, g, h = src_to_dst
    warped: List[Tuple[float, float, float, float, str]] = []
    for ln in lines:
        corners = []
        for corner in [
            ln.top_left,
            ln.top_right,
            ln.bottom_right,
            ln.bottom_left,
        ]:
            rx = corner["x"] * receipt_w
            # Flip OCR y (0=bottom) -> image y (0=top)
            ry = (1.0 - corner["y"]) * receipt_h
            x_img, y_img = _warp_point(coeffs, rx, ry)
            corners.append((x_img, y_img))

        warped_pts = []
        for x, y in corners:
            den = g * x + h * y + 1.0
            if abs(den) < 1e-12:
                warped_pts.append((x, y))
            else:
                warped_pts.append(
                    ((a * x + b * y + c) / den, (d * x + e * y + f) / den)
                )
        xs = [p[0] for p in warped_pts]
        ys = [p[1] for p in warped_pts]
        cx = sum(xs) / 4.0
        cy = sum(ys) / 4.0
        top = min(ys)
        bottom = max(ys)
        warped.append((top, bottom, cx, cy, ln.text))
    return warped


def _group_rows_centroid_span(
    entries: List[Tuple[float, float, float, float, str]],
) -> str:
    """
    Group lines: mutual centroid-in-span, ordered top->bottom (cy), then left->right.
    entries: (top, bottom, cx, cy, text)
    """
    # Sort by cy, then cx
    entries.sort(key=lambda t: (t[3], t[2]))
    rows: List[List[Tuple[float, float, float, float, str]]] = []
    for top, bottom, cx, cy, text in entries:
        if not rows:
            rows.append([(top, bottom, cx, cy, text)])
            continue
        prev_row = rows[-1]
        prev_top = min(p[0] for p in prev_row)
        prev_bottom = max(p[1] for p in prev_row)
        prev_cy = prev_row[-1][3]

        in_prev = prev_top <= cy <= prev_bottom
        in_curr = top <= prev_cy <= bottom
        if in_prev or in_curr:
            prev_row.append((top, bottom, cx, cy, text))
        else:
            rows.append([(top, bottom, cx, cy, text)])

    rendered: List[str] = []
    line_id = 1
    for row in rows:
        row_sorted = sorted(row, key=lambda t: t[2])  # cx
        texts = [r[4] for r in row_sorted if r[4]]
        rendered.append(f"{line_id}: {' '.join(texts)}")
        line_id += 1
    return "\n".join(rendered)


def _format_combined_text_warped(
    receipt_a: Receipt,
    lines_a: Sequence[ReceiptLine],
    receipt_b: Receipt,
    lines_b: Sequence[ReceiptLine],
    image_width: int,
    image_height: int,
) -> str:
    """Build combined text using warped, re-ID'ed lines."""

    def _collect(
        receipt: Receipt, lines: Sequence[ReceiptLine]
    ) -> List[Tuple[float, float]]:
        coeffs, rw, rh = get_receipt_to_image_transform(
            receipt, image_width, image_height
        )
        res = []
        for ln in lines:
            for corner in [
                ln.top_left,
                ln.top_right,
                ln.bottom_right,
                ln.bottom_left,
            ]:
                rx = corner["x"] * rw
                ry = corner["y"] * rh
                res.append(_warp_point(coeffs, rx, ry))
        return res

    all_pts = _collect(receipt_a, lines_a) + _collect(receipt_b, lines_b)
    if not all_pts:
        return ""

    (cx, cy), (rw, rh), angle = min_area_rect(all_pts)
    src_corners = reorder_box_points(box_points((cx, cy), (rw, rh), angle))
    dst = [(0, 0), (rw - 1, 0), (rw - 1, rh - 1), (0, rh - 1)]
    pil_coeffs = find_perspective_coeffs(src_points=src_corners, dst_points=dst)
    src_to_dst = invert_warp(*pil_coeffs)

    warped_entries = []
    warped_entries.extend(
        _warp_lines_to_common_space(
            receipt_a, lines_a, image_width, image_height, src_to_dst
        )
    )
    warped_entries.extend(
        _warp_lines_to_common_space(
            receipt_b, lines_b, image_width, image_height, src_to_dst
        )
    )
    # Sort top->bottom, left->right now that y is in image space (0=top)
    return _group_rows_centroid_span(warped_entries)


def _warp_lines_to_image_space(
    receipt: Receipt,
    lines: Sequence[ReceiptLine],
    image_width: int,
    image_height: int,
) -> List[Dict[str, Any]]:
    """
    Warp receipt lines into the original image coordinate system.

    Mirrors the transform path used elsewhere (min-area rect → ReceiptLines)
    so ordering matches how other agents (e.g., receipt_label) render text.
    """
    try:
        coeffs, receipt_w, receipt_h = receipt.get_transform_to_image(
            image_width, image_height
        )
    except Exception:
        return []

    warped_lines: List[Dict[str, Any]] = []
    for ln in lines:
        pts = [
            ln.top_left,
            ln.top_right,
            ln.bottom_right,
            ln.bottom_left,
        ]
        warped_corners: List[Tuple[float, float]] = []
        for pt in pts:
            rx = pt["x"] * receipt_w
            ry = pt["y"] * receipt_h
            warped_corners.append(_warp_point(coeffs, rx, ry))

        cx = sum(p[0] for p in warped_corners) / 4.0
        cy = sum(p[1] for p in warped_corners) / 4.0
        xs = [p[0] for p in warped_corners]
        ys = [p[1] for p in warped_corners]
        warped_lines.append(
            {
                "receipt_id": receipt.receipt_id,
                "line_id": ln.line_id,
                "text": ln.text,
                "centroid_x": cx,
                "centroid_y": cy,
                "left_x": min(xs),
                "right_x": max(xs),
                "top_y": min(ys),
                "bottom_y": max(ys),
            }
        )

    return warped_lines


def _format_id_range(ids: Sequence[int]) -> str:
    """Format a list of line IDs as a single range or comma list."""
    unique_ids = sorted(set(ids))
    if len(unique_ids) == 1:
        return f"{unique_ids[0]}:"
    is_consecutive = all((b - a) == 1 for a, b in zip(unique_ids, unique_ids[1:]))
    if is_consecutive:
        return f"{unique_ids[0]}-{unique_ids[-1]}:"
    return f"{','.join(str(i) for i in unique_ids)}:"


def _group_warped_lines(
    warped_lines: Sequence[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """
    Group lines that sit on the same visual row in image space using
    vertical-span overlap (mirrors receipt_label/_format_receipt_lines logic).
    """
    if not warped_lines:
        return []

    sorted_lines = sorted(
        warped_lines, key=lambda l: (l["centroid_y"], l["centroid_x"])
    )
    groups: List[List[Dict[str, Any]]] = [[sorted_lines[0]]]

    for prev, curr in zip(sorted_lines, sorted_lines[1:]):
        prev_top = prev["top_y"]
        prev_bottom = prev["bottom_y"]
        prev_height = max(prev_bottom - prev_top, 1.0)
        tolerance = max(prev_height * 0.1, 2.0)  # small slack
        cy = curr["centroid_y"]
        # Same row if centroid lies within (or slightly around) the previous vertical span
        if (prev_top - tolerance) <= cy <= (prev_bottom + tolerance):
            groups[-1].append(curr)
        else:
            groups.append([curr])

    # Normalize left-to-right ordering inside each row
    normalized_groups: List[List[Dict[str, Any]]] = []
    for grp in groups:
        normalized_groups.append(
            sorted(grp, key=lambda l: (l["centroid_x"], l["line_id"]))
        )
    return normalized_groups


def _format_grouped_warped_lines(
    warped_lines: Sequence[Dict[str, Any]],
) -> str:
    """Render warped lines grouped by visual rows (line-id ranges preserved)."""
    if not warped_lines:
        return ""

    grouped = _group_warped_lines(warped_lines)
    formatted: List[str] = []
    for grp in grouped:
        ids = [ln["line_id"] for ln in grp]
        text = " ".join(ln["text"] for ln in grp)
        formatted.append(f"{_format_id_range(ids)} {text}")
    return "\n".join(formatted)


def _format_receipt_text_image_space(
    receipt: Receipt,
    lines: Sequence[ReceiptLine],
    image_width: int,
    image_height: int,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Format receipt text the same way other agents do.

    - Warp to image space (shared frame after min-area rect)
    - Group visually contiguous lines
    - Prefix each row with its line-id range
    """
    warped = _warp_lines_to_image_space(receipt, lines, image_width, image_height)
    if warped:
        return _format_grouped_warped_lines(warped), warped

    # Fallback: OCR-space ordering if transform fails
    sorted_lines = _sort_lines_ocr_space(lines)
    fallback = [f"{ln.line_id}: {ln.text}" for ln in sorted_lines]
    return "\n".join(fallback), []


def _format_combined_warped_lines(
    lines_a: Sequence[Dict[str, Any]],
    lines_b: Sequence[Dict[str, Any]],
) -> str:
    """Render both receipts together, grouping lines that share the same row."""
    merged = sorted(
        list(lines_a) + list(lines_b),
        key=lambda l: (l["centroid_y"], l["centroid_x"]),
    )
    if not merged:
        return ""

    grouped_rows: List[List[Dict[str, Any]]] = []
    grouped_rows.append([merged[0]])
    for prev, curr in zip(merged, merged[1:]):
        prev_top = prev["top_y"]
        prev_bottom = prev["bottom_y"]
        prev_height = max(prev_bottom - prev_top, 1.0)
        tolerance = max(prev_height * 0.1, 2.0)
        cy = curr["centroid_y"]
        if (prev_top - tolerance) <= cy <= (prev_bottom + tolerance):
            grouped_rows[-1].append(curr)
        else:
            grouped_rows.append([curr])

    rendered_rows = []
    for row in grouped_rows:
        row_sorted = sorted(row, key=lambda l: (l["centroid_x"], l["line_id"]))
        row_text = " ".join(
            f"[r{ln['receipt_id']} l{ln['line_id']}] {ln['text']}" for ln in row_sorted
        )
        rendered_rows.append(f"- {row_text}")

    return "\n".join(rendered_rows)


def _format_combined_text_ocr(
    lines_a: Sequence[ReceiptLine],
    lines_b: Sequence[ReceiptLine],
) -> str:
    """
    Fallback combined rendering using OCR-space ordering (top-to-bottom, left-to-right),
    grouping lines that share the same visual row (receipt_label-style).
    """
    merged = _sort_lines_ocr_space(list(lines_a) + list(lines_b))
    if not merged:
        return ""

    grouped_rows: List[List[ReceiptLine]] = []
    grouped_rows.append([merged[0]])
    for prev, curr in zip(merged, merged[1:]):
        _, cy = curr.calculate_centroid()
        # OCR space: y increases toward top (top=1, bottom=0)
        if prev.bottom_left["y"] < cy < prev.top_left["y"]:
            grouped_rows[-1].append(curr)
        else:
            grouped_rows.append([curr])

    rendered_rows = []
    for row in grouped_rows:
        row_sorted = sorted(row, key=lambda l: (l.calculate_centroid()[0], l.line_id))
        row_text = " ".join(
            f"[r{ln.receipt_id} l{ln.line_id}] {ln.text}" for ln in row_sorted
        )
        rendered_rows.append(f"- {row_text}")

    return "\n".join(rendered_rows)


class ReceiptCombinationSelector:
    """
    Build candidate combinations and ask an LLM to choose the best one.
    """

    def __init__(self, dynamo: DynamoClient, llm_client: Any | None = None) -> None:
        self.dynamo = dynamo
        # Hydrate env from Pulumi if available (local convenience)
        try:
            stack = os.environ.get("PULUMI_STACK", "dev")
            env = load_env(stack, working_dir="infra")
            secrets = load_secrets(stack, working_dir="infra")
            table = env.get("dynamodb_table_name") or env.get("receipts_table_name")
            if table:
                os.environ.setdefault("DYNAMODB_TABLE_NAME", table)
            ollama_key = secrets.get("OLLAMA_API_KEY") or secrets.get(
                "portfolio:OLLAMA_API_KEY"
            )
            if ollama_key:
                os.environ.setdefault("RECEIPT_AGENT_OLLAMA_API_KEY", ollama_key)
            langchain_key = secrets.get("LANGCHAIN_API_KEY") or secrets.get(
                "portfolio:LANGCHAIN_API_KEY"
            )
            if langchain_key:
                os.environ.setdefault("LANGCHAIN_API_KEY", langchain_key)
        except Exception:
            pass

        # Use provided client or default Ollama client.
        settings = get_settings()
        if llm_client:
            self.llm = llm_client
        else:
            api_key = settings.ollama_api_key.get_secret_value() or ""
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            self.llm = ChatOllama(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                temperature=0,
                client_kwargs={
                    "headers": headers,
                    "timeout": 120,
                },
            )

    def build_candidates(
        self, image_id: str, target_receipt_id: int
    ) -> List[Dict[str, Any]]:
        """Create pairwise candidates: (target, other) for all other receipts."""
        image = self.dynamo.get_image(image_id)
        receipts = self.dynamo.get_receipts_from_image(image_id)
        target = next((r for r in receipts if r.receipt_id == target_receipt_id), None)
        if not target:
            raise ValueError(
                f"Target receipt {target_receipt_id} not found for {image_id}"
            )
        others = [r for r in receipts if r.receipt_id != target_receipt_id]
        if not others:
            return []
        image_width = image.width
        image_height = image.height
        candidates = []
        for other in others:
            lines_target = self.dynamo.list_receipt_lines_from_receipt(
                image_id, target.receipt_id
            )
            lines_other = self.dynamo.list_receipt_lines_from_receipt(
                image_id, other.receipt_id
            )
            text_combined = _format_combined_text_warped(
                target,
                lines_target,
                other,
                lines_other,
                image_width,
                image_height,
            )
            if not text_combined:
                # Fallback to simple OCR ordering
                text_combined = _format_combined_text_ocr(lines_target, lines_other)
            candidates.append(
                {
                    "combo": [target.receipt_id, other.receipt_id],
                    "text_combined": text_combined,
                }
            )
        return candidates

    def _build_prompt(self, candidates: Sequence[Dict[str, Any]]) -> str:
        """Build a deterministic, JSON-only prompt."""
        lines = [
            "You must decide if any candidate pair is the SAME transaction.",
            "Pick exactly one OPTION number that best matches, or -1 if none.",
            "",
            "Decision rules (strict):",
            "- Reject if merchants/addresses conflict.",
            "- Reject if dates conflict (different day) or times are far apart.",
            "- Reject if totals or card tails conflict.",
            "- Prefer the pair with the strongest overlap in this order:",
            "  1) order/ticket id",
            "  2) card tail",
            "  3) date/time (same day, small drift allowed)",
            "  4) totals (subtotal/tax/tip/total)",
            "  5) merchant/address string match",
            "",
            "Output JSON ONLY with this exact shape:",
            '{"choice": <int or -1>, "reason": "<1-3 sentences citing fields>"}',
            "Never return null. Use -1 if no option satisfies the rules.",
            "",
            "Receipt text is ordered in image space with source ids (rX lY).",
            "",
        ]
        for idx, cand in enumerate(candidates, start=1):
            a, b = cand["combo"]
            lines.append(f"OPTION {idx}: receipt {a} + receipt {b}")
            combined_text = cand.get("text_combined") or ""
            if combined_text:
                lines.append(
                    "Combined receipt lines (image order, grouped rows, with source ids):"
                )
                lines.append(combined_text)
            else:
                # Fallback: show individual receipts if combined text is missing
                lines.append("Receipt A lines (image order, grouped):")
                lines.append(cand["text_target"])
                lines.append("Receipt B lines (image order, grouped):")
                lines.append(cand["text_other"])
            lines.append("")
        lines.append(
            'Return JSON only. Example: {"choice": 2, "reason": "Same merchant, same order id 781185, same card tail 3931."} or {"choice": -1, "reason": "Conflicting dates and merchants."}'
        )
        return "\n".join(lines)

    def choose(self, image_id: str, target_receipt_id: int) -> Dict[str, Any]:
        """Return the chosen combination or NONE."""
        candidates = self.build_candidates(image_id, target_receipt_id)
        if not candidates:
            return {"status": "no_candidates", "choice": None}

        # Prepare minimal, privacy-aware trace payload (no full text)
        candidate_pairs = [c["combo"] for c in candidates]
        prompt = self._build_prompt(candidates)
        prompt_len = len(prompt)

        prompt_version = "combine-receipts-v2"
        # Minimal LangSmith-friendly metadata/tags per LC docs
        prompt = self._build_prompt(candidates)
        result = self.llm.invoke(
            prompt,
            config={
                "tags": [
                    "combine-receipts",
                    f"image:{image_id}",
                    f"target:{target_receipt_id}",
                    os.environ.get("PULUMI_STACK", "dev"),
                ],
                "metadata": {
                    "image_id": image_id,
                    "target_receipt_id": target_receipt_id,
                    "candidate_pairs": candidate_pairs,
                    "prompt_length": prompt_len,
                    "prompt_version": prompt_version,
                },
            },
        )
        raw_answer = result.content if hasattr(result, "content") else str(result)

        choice = None
        try:
            parsed = json.loads(raw_answer)
            pick = parsed.get("choice")
            if isinstance(pick, int):
                if pick == -1:
                    choice = None
                elif 1 <= pick <= len(candidates):
                    choice = candidates[pick - 1]["combo"]
        except Exception:
            choice = None

        outputs = {
            "status": "ok",
            "choice": choice,
            "raw_answer": raw_answer,
            "candidates": candidate_pairs,
            "image_id": image_id,
            "target_receipt_id": target_receipt_id,
        }
        return outputs
