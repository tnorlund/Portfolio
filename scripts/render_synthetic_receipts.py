#!/usr/bin/env python3.12
"""Render synthetic receipt candidates (and their real base) to PNGs for QA.

Reads a bundle produced by ``verify_synthetic_replay.py local-pipeline`` plus the
real receipt export directory it was built from, then renders each accepted
synthetic training example beside the real receipt it was derived from using the
font-render renderer. This is the visual-QA artifact for milestone 2 of the
``feat/receipt-font-render`` charter — it consumes data only and touches no gate.

Usage:
    python3.12 scripts/render_synthetic_receipts.py \
        --bundle .tmp/bundle.json \
        --receipt-dir .tmp/vons_export \
        --out-dir .tmp/render \
        --merchant Vons
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import sys
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (
    os.path.join(REPO_ROOT, "receipt_agent"),
    os.path.join(REPO_ROOT, "receipt_upload"),
):
    if path not in sys.path:
        sys.path.insert(0, path)

from receipt_agent.agents.label_evaluator.rendering import (  # noqa: E402
    GlyphRenderConfig,
    RenderConfig,
    build_glyph_atlas_from_dynamo,
    build_merchant_font_profile,
    build_merchant_font_profile_from_dynamo,
    extract_receipt_font_profile,
    make_ttf_fallback,
    render_receipt,
    save_receipt_glyphs,
    render_real_vs_synthetic,
    save_receipt_png,
)
from receipt_agent.agents.label_evaluator.rendering import (  # noqa: E402
    receipt_graphics,
)
from receipt_agent.agents.label_evaluator.rendering.content_clean import (  # noqa: E402
    clean_for_render,
)


def _bbox_from_bounding_box(bb: dict) -> list[float] | None:
    try:
        x = float(bb["x"])
        y = float(bb["y"])
        w = float(bb["width"])
        h = float(bb["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return [x, y, x + w, y + h]


def _real_receipt_dict(export: dict, receipt_id: int) -> dict:
    """Build a renderer receipt dict from an exported receipt's OCR words."""
    label_index: dict[tuple, list[str]] = {}
    for lbl in export.get("receipt_word_labels", []) or []:
        if lbl.get("receipt_id") != receipt_id:
            continue
        key = (lbl.get("line_id"), lbl.get("word_id"))
        label_index.setdefault(key, []).append(str(lbl.get("label") or ""))

    words = []
    for word in export.get("receipt_words", []) or []:
        if word.get("receipt_id") != receipt_id:
            continue
        bbox = _bbox_from_bounding_box(word.get("bounding_box") or {})
        if bbox is None:
            continue
        key = (word.get("line_id"), word.get("word_id"))
        words.append(
            {
                "text": word.get("text", ""),
                "bbox": bbox,
                "labels": label_index.get(key, []),
                "line_id": word.get("line_id"),
                "word_id": word.get("word_id"),
            }
        )
    return {"words": words}


def _resolve_tag(tag, id_to_label: dict[int, str]):
    """ner_tags may be BIO strings ('B-PRODUCT_NAME') or integer ids."""
    if isinstance(tag, str):
        return tag
    if isinstance(tag, int):
        return id_to_label.get(tag)
    return None


def _synthetic_receipt_dict(example: dict, id_to_label: dict[int, str]) -> dict:
    words = []
    tokens = example.get("tokens") or []
    bboxes = example.get("bboxes") or []
    tags = example.get("ner_tags") or []
    for index, (token, bbox) in enumerate(zip(tokens, bboxes)):
        label = _resolve_tag(tags[index], id_to_label) if index < len(tags) else None
        # Keep the raw (possibly BIO-prefixed) label; the renderer normalizes it.
        words.append(
            {
                "text": token,
                "bbox": bbox,
                "labels": [label] if label and label != "O" else [],
            }
        )
    receipt: dict = {"words": words}
    # Preserve the merchant so the graphics pass can pick a sensible barcode
    # symbology (grocery UPC-A vs. transaction Code128).
    merchant_name = example.get("merchant_name")
    if merchant_name:
        receipt["merchant_name"] = merchant_name
    return receipt


# Header lines that despite starting with the brand token are NOT part of the
# store header block (feedback/survey/dot-com/gift-card footers).
_HEADER_BRAND_EXCLUDE = ("FEEDBACK", "COM", "GIFT")


def _header_profile_for(merchant: str | None) -> dict:
    """Header-handling profile for a merchant: whether the cached OCR repeats the
    store header (``dedup``) and interleaves department sections that need
    reordering (``reflow``), plus the phrases that identify header lines and the
    body-section anchors. The brand token is derived from the merchant name so no
    merchant string is hardcoded. Empty/absent -> no dedup/reflow (the default for
    merchants whose cached OCR is already clean)."""
    h = get_merchant_profile(merchant).get("header", {}) or {}
    first = (merchant or "").split()
    return {
        "brand": _compact_text(first[0]) if first else "",
        # exact = whole-(compacted-)line markers (a standalone FARMERS MARKET /
        # phone / hours line); contains = substring markers (a street the address
        # lines share, e.g. WESTLAKE). The split preserves that "FARMERS MARKET
        # SAVINGS!" is NOT a header line while "1012 WESTLAKE BLVD." is.
        "exact": {str(m).upper() for m in h.get("header_exact", [])},
        "contains": [str(m).upper() for m in h.get("header_contains", [])],
        "body_anchors": set(h.get("body_section_anchors", [])),
        "reflow": bool(h.get("reflow_sections")),
        "dedup": bool(h.get("header_dedup")),
    }


def _cached_token_receipt_dict(example: dict) -> dict:
    merchant = example.get("merchant_name")
    hp = _header_profile_for(merchant)
    words = []
    for token, bbox, tag in zip(
        example.get("tokens") or [],
        example.get("bboxes") or [],
        example.get("ner_tags") or [],
    ):
        normalized_bbox = _normalize_bbox(bbox)
        if normalized_bbox is None:
            continue
        label = _resolve_tag(tag, {})
        words.append(
            {
                "text": token,
                "bbox": normalized_bbox,
                "labels": [label] if label and label != "O" else [],
            }
        )
    words = _drop_duplicate_header_words(words, hp)
    if hp["reflow"] and hp["brand"] and any(
        hp["brand"] in _compact_line_text(line)
        for line in _group_cached_words_by_line(words)
    ):
        return _line_receipt_from_cached_token_words(words, merchant)
    return {"words": words}


# Cached line-only renderer layout, in the 0..1000 coordinate space. These are
# GENERIC estimates for the width-less line/token examples (no OCR word widths to
# measure), NOT per-merchant geometry -- the hybrid/grid path measures real
# pitch/advance/price-column from the font profile instead.
_PRICE_COLUMN_RIGHT = 905.0   # right edge the price column is anchored to
_LINE_ROW_PITCH = 16.0        # vertical step between synthesized line rows
_LINE_CHAR_WIDTH = 12.0       # width estimate per character (no measured widths)
_LINE_WORD_GAP = 8.0          # horizontal gap between words
_LINE_LOGO_MIN_WIDTH = 220.0  # floor width for a single-token logo line
_LINE_MAX_WIDTH = 900.0       # content width the row is scaled to fit within
# Trailing price/amount tokens: optional leading currency / sign, a decimal
# amount with two fractional digits, optional trailing sign (e.g. "1.99-").
_PRICE_TOKEN_RE = re.compile(r"^[-+]?\$?\d{1,3}(?:,\d{3})*\.\d{2}[-+]?$")


def _is_price_token(token: str) -> bool:
    return bool(_PRICE_TOKEN_RE.match(str(token or "").strip()))


def _cached_line_receipt_dict(example: dict) -> dict:
    hp = _header_profile_for(example.get("merchant_name"))
    lines = []
    source_lines = _order_cached_lines(
        _drop_duplicate_header_lines(example, hp), hp
    )
    for index, line in enumerate(source_lines):
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        words = text.split()
        y = float(line.get("y") or (940 - index * _LINE_ROW_PITCH))
        labels = list(line.get("labels") or [])
        is_logo_line = any(_label_name(label) == "MERCHANT_NAME" for label in labels)
        # Cached line-only examples do not carry OCR word widths, so give the
        # renderer enough horizontal room to avoid shrinking every line to the
        # minimum font size.
        width_units = [max(18.0, len(word) * _LINE_CHAR_WIDTH) for word in words]
        if is_logo_line and len(words) == 1:
            width_units = [max(width_units[0], _LINE_LOGO_MIN_WIDTH)]
        total_width = sum(width_units) + max(0, len(words) - 1) * _LINE_WORD_GAP
        if total_width > _LINE_MAX_WIDTH:
            factor = _LINE_MAX_WIDTH / total_width
            width_units = [width * factor for width in width_units]
            total_width = _LINE_MAX_WIDTH
        if is_logo_line:
            x = 500 - total_width / 2
        else:
            x = 70
        half_height = 24 if is_logo_line else 6
        # Preserve a right-aligned price column: if a body item line ends with a
        # currency/decimal token, anchor that trailing token's right edge to the
        # fixed price column instead of letting it butt against the item name.
        price_index = None
        if (
            not is_logo_line
            and len(words) >= 2
            and _is_price_token(words[-1])
        ):
            price_index = len(words) - 1
            price_width = width_units[price_index]
            price_x0 = max(x, _PRICE_COLUMN_RIGHT - price_width)
        rendered_words = []
        for word_index, (word, width) in enumerate(zip(words, width_units)):
            if word_index == price_index:
                bbox = [
                    price_x0,
                    y - half_height,
                    price_x0 + price_width,
                    y + half_height,
                ]
            else:
                bbox = [x, y - half_height, x + width, y + half_height]
                x += width + _LINE_WORD_GAP
            rendered_words.append(
                {
                    "text": word,
                    "bbox": bbox,
                    "labels": labels,
                }
            )
        lines.append({"line_id": index + 1, "words": rendered_words})
    return {"lines": lines}


def _normalize_bbox(bbox: list[float] | tuple[float, ...]) -> list[float] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in bbox[:4])
    except (TypeError, ValueError):
        return None
    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))
    if right <= left or top <= bottom:
        return None
    return [left, bottom, right, top]


def _drop_duplicate_header_lines(example: dict, hp: dict) -> list[dict]:
    """Keep one store header/address block in cached line-only renders."""
    if not hp["dedup"]:
        return list(example.get("lines") or [])
    seen: set[str] = set()
    kept = []
    for line in example.get("lines") or []:
        text = _compact_text(str(line.get("text") or ""))
        if _is_header_line(text, hp):
            if text in seen:
                continue
            seen.add(text)
        kept.append(line)
    return kept


def _order_cached_lines(lines: list[dict], hp: dict) -> list[dict]:
    if not (hp["reflow"] and hp["brand"] and any(
        hp["brand"] in _compact_text(line.get("text") or "") for line in lines
    )):
        return lines

    sections: dict[str, list[dict]] = {
        "header": [],
        "body": [],
        "payment": [],
        "footer": [],
    }
    for line in lines:
        sections[_text_section(_compact_text(line.get("text") or ""), hp)].append(line)

    ordered = []
    for name in ("header", "body", "payment", "footer"):
        if ordered and sections[name]:
            ordered.append({"text": "", "y": None, "labels": []})
        ordered.extend(sections[name])

    real_count = sum(1 for line in ordered if str(line.get("text") or "").strip())
    break_count = len(ordered) - real_count
    section_gap = 18.0 if real_count < 70 else 10.0
    spacing = (930.0 - break_count * section_gap) / max(1, real_count - 1)
    spacing = max(9.0, min(15.0, spacing))

    y = 978.0
    positioned = []
    for line in ordered:
        text = str(line.get("text") or "").strip()
        if not text:
            y -= section_gap
            continue
        item = dict(line)
        item["y"] = y
        positioned.append(item)
        y -= spacing
    return positioned


def _drop_duplicate_header_words(words: list[dict], hp: dict) -> list[dict]:
    """Remove repeated store header/address blocks from cached examples."""
    if not hp["dedup"]:
        return words
    line_groups: dict[int, list[dict]] = {}
    for word in words:
        bbox = word.get("bbox")
        if not bbox:
            continue
        y = round((float(bbox[1]) + float(bbox[3])) / 20) * 10
        line_groups.setdefault(int(y), []).append(word)

    product_y = None
    for y, line_words in line_groups.items():
        text = _compact_line_text(line_words)
        if text in hp["body_anchors"]:
            product_y = max(product_y or y, y)

    seen: set[str] = set()
    drop_ids: set[int] = set()
    for y in sorted(line_groups, reverse=True):
        line_words = line_groups[y]
        text = _compact_line_text(line_words)
        if product_y is not None and y <= product_y:
            continue
        if not _is_header_line(text, hp):
            continue
        if text in seen:
            drop_ids.update(id(word) for word in line_words)
            continue
        seen.add(text)

    return [word for word in words if id(word) not in drop_ids]


def _repair_missing_top_header_lines(receipt: dict) -> dict:
    """Clone OCR-missed header lines from a later duplicate header block.

    Some Sprouts photos visibly print the store-hours line in the top header, but
    OCR only captures that same line in the repeated lower address block. When a
    merchant profile identifies duplicate header lines, use the lower complete
    block to fill missing exact header markers in the top block. This is still
    data-driven: the cloned text and x-geometry come from Dynamo OCR, while the
    y-position is transferred by matching a shared header anchor such as the
    phone line.
    """
    hp = _header_profile_for(receipt.get("merchant_name"))
    if not hp["dedup"]:
        return receipt
    words = list(receipt.get("words") or [])
    if not words:
        return receipt

    line_infos = []
    for line in _group_cached_words_by_line(words):
        text = _compact_line_text(line)
        line_infos.append({
            "words": line,
            "text": text,
            "center": _line_center_y(line),
            "is_header": _is_header_line(text, hp),
        })
    try:
        first_header = next(
            index for index, info in enumerate(line_infos) if info["is_header"]
        )
    except StopIteration:
        return receipt

    top_end = first_header
    while top_end + 1 < len(line_infos) and line_infos[top_end + 1]["is_header"]:
        top_end += 1
    top_block = line_infos[first_header:top_end + 1]
    top_texts = {info["text"] for info in top_block}
    exact_markers = set(hp.get("exact") or ())
    missing = [
        marker for marker in exact_markers
        if marker not in top_texts
        and any(info["text"] == marker for info in line_infos[top_end + 1:])
    ]
    if not missing:
        return receipt

    additions: list[dict] = []
    for marker in missing:
        source_index = next(
            index for index, info in enumerate(line_infos[top_end + 1:], top_end + 1)
            if info["text"] == marker
        )
        source = line_infos[source_index]
        anchor = None
        for candidate in reversed(line_infos[top_end + 1:source_index]):
            if candidate["is_header"] and candidate["text"] in top_texts:
                anchor = candidate
                break
        target_center = None
        if anchor is not None:
            target_anchor = next(
                info for info in top_block if info["text"] == anchor["text"]
            )
            target_center = (
                float(target_anchor["center"])
                + float(source["center"])
                - float(anchor["center"])
            )
        if target_center is None:
            centers = [float(info["center"]) for info in top_block]
            gaps = [
                abs(a - b)
                for a, b in zip(sorted(centers, reverse=True), sorted(centers, reverse=True)[1:])
            ]
            pitch = sorted(gaps)[len(gaps) // 2] if gaps else 16.0
            target_center = min(centers) - pitch
        shift = float(target_center) - float(source["center"])
        source_box = _union_bbox([
            word["bbox"] for word in source["words"] if word.get("bbox")
        ])
        source_cx = (
            (float(source_box[0]) + float(source_box[2])) / 2.0
            if source_box is not None else 0.0
        )
        source_span = (
            max(1e-6, float(source_box[2]) - float(source_box[0]))
            if source_box is not None else 1.0
        )
        x_scale = 1.0
        if anchor is not None:
            target_anchor = next(
                info for info in top_block if info["text"] == anchor["text"]
            )
            source_anchor_box = _union_bbox([
                word["bbox"] for word in anchor["words"] if word.get("bbox")
            ])
            target_anchor_box = _union_bbox([
                word["bbox"] for word in target_anchor["words"] if word.get("bbox")
            ])
            if source_anchor_box is not None and target_anchor_box is not None:
                source_anchor_span = max(
                    1e-6, float(source_anchor_box[2]) - float(source_anchor_box[0])
                )
                target_anchor_span = max(
                    1e-6, float(target_anchor_box[2]) - float(target_anchor_box[0])
                )
                x_scale = target_anchor_span / source_anchor_span
        top_spans = []
        for info in top_block:
            if info["text"].startswith(hp["brand"]):
                continue
            box = _union_bbox([
                word["bbox"] for word in info["words"] if word.get("bbox")
            ])
            if box is not None:
                top_spans.append(float(box[2]) - float(box[0]))
        if top_spans:
            max_target_span = max(top_spans) * 1.15
            x_scale = min(x_scale, max_target_span / source_span)
        for word in source["words"]:
            bbox = list(word.get("bbox") or [])
            if len(bbox) < 4:
                continue
            try:
                x0, y0, x1, y1 = (float(value) for value in bbox[:4])
            except (TypeError, ValueError):
                continue
            clone = dict(word)
            clone["bbox"] = [
                source_cx + (x0 - source_cx) * x_scale,
                y0 + shift,
                source_cx + (x1 - source_cx) * x_scale,
                y1 + shift,
            ]
            clone["line_id"] = f"header-clone-{word.get('line_id', '')}"
            clone["word_id"] = f"header-clone-{word.get('word_id', '')}"
            clone["_synthetic_source"] = "duplicate_header_repair"
            additions.append(clone)

    if not additions:
        return receipt
    repaired = dict(receipt)
    repaired["words"] = words + additions
    return repaired


def _compact_line_text(line_words: list[dict]) -> str:
    text = "".join(str(word.get("text") or "") for word in line_words)
    return _compact_text(text)


def _compact_text(text: str) -> str:
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def _label_name(label: str) -> str:
    text = str(label or "").upper()
    if text.startswith(("B-", "I-")):
        return text[2:]
    return text


def _is_header_line(text: str, hp: dict) -> bool:
    """True for a store header/address line: the brand wordmark (minus footer
    false-positives) or one of the merchant's configured header markers."""
    brand = hp["brand"]
    is_brand_line = bool(brand) and text.startswith(brand) and not any(
        marker in text for marker in _HEADER_BRAND_EXCLUDE
    )
    return (
        is_brand_line
        or text in hp["exact"]
        or any(marker in text for marker in hp["contains"])
    )


def _line_receipt_from_cached_token_words(
    words: list[dict], merchant: str | None
) -> dict:
    """Convert cached token boxes into a legible public line-render receipt."""
    ordered = _ordered_token_lines(words, _header_profile_for(merchant))
    if not ordered:
        return {"words": words}

    line_count = sum(1 for _, is_break in ordered if not is_break)
    break_count = sum(1 for _, is_break in ordered if is_break)
    section_gap = 16.0 if line_count < 76 else 10.0
    spacing = (930.0 - break_count * section_gap) / max(1, line_count - 1)
    spacing = max(9.0, min(14.0, spacing))

    y = 978.0
    lines = []
    for line, is_break in ordered:
        if is_break:
            y -= section_gap
            continue
        labels = sorted(
            {
                label
                for word in line
                for label in (word.get("labels") or [])
                if label
            }
        )
        text = _line_text_from_cached_words(line)
        if text:
            lines.append({"y": y, "text": text, "labels": labels})
            y -= spacing
    return _cached_line_receipt_dict({"lines": lines, "merchant_name": merchant})


def _ordered_token_lines(
    words: list[dict], hp: dict
) -> list[tuple[list[dict], bool]]:
    lines = _group_cached_words_by_line(words)
    if not lines:
        return []
    sections: dict[str, list[list[dict]]] = {
        "header": [],
        "body": [],
        "payment": [],
        "footer": [],
    }
    for line in sorted(lines, key=_line_center_y, reverse=True):
        sections[_token_section(line, hp)].append(line)

    ordered: list[tuple[list[dict], bool]] = []
    for name in ("header", "body", "payment", "footer"):
        if not sections[name]:
            continue
        if ordered:
            ordered.append(([], True))
        ordered.extend((line, False) for line in sections[name])
    return ordered


def _line_text_from_cached_words(line: list[dict]) -> str:
    return " ".join(
        str(word.get("text") or "").strip()
        for word in sorted(line, key=lambda item: float(item["bbox"][0]))
        if str(word.get("text") or "").strip()
    )


def _group_cached_words_by_line(words: list[dict]) -> list[list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for word in words:
        bbox = word.get("bbox")
        if not bbox:
            continue
        y = round((float(bbox[1]) + float(bbox[3])) / 16) * 16
        grouped.setdefault(int(y), []).append(word)
    return [
        sorted(line_words, key=lambda word: float(word["bbox"][0]))
        for _, line_words in sorted(grouped.items(), reverse=True)
    ]


def _line_center_y(line: list[dict]) -> float:
    return sum((float(word["bbox"][1]) + float(word["bbox"][3])) / 2 for word in line) / len(line)


def _token_section(line: list[dict], hp: dict) -> str:
    text = _compact_line_text(line)
    return _text_section(text, hp)


def _text_section(text: str, hp: dict) -> str:
    if _is_header_line(text, hp):
        return "header"
    if any(token in text for token in (
        "FEEDBACK",
        "SURVEY",
        "SPROUTSFEEDBACK",
        "WINNERS",
        "CASHIER",
        "POSTRANSACTION",
        "TRANSACTION",
        "SIGNUP",
        "RECEIVE",
        "WEEKLYAD",
        "EMAILATSPROUTS",
        "PLEASEKEEP",
        "PAYMENTUSED",
        "RETURNS",
        "RECEIPT",
        "REWARDS",
        "CHAN",
        "MONTHLY",
        "SPROUTSCOM",
        "SAVE",
        "PAPER",
        "EMAIL",
        "TYPEOFCREDIT",
        "METHODOFPAYMENT",
        "WITHOUT",
        "LIMITS",
        "APPLY",
        "WIN",
        "GIFT",
    )):
        return "footer"
    if any(token in text for token in (
        "DEBIT",
        "CREDIT",
        "MASTERCARD",
        "PURCHASE",
        "APPROVED",
        "AUTHCODE",
        "ENTRYMETHOD",
        "ENTRY",
        "METHOD",
        "CARD",
        "TOTALUSD",
        "TOTAL",
        "USD",
        "BALANCEDUE",
        "BALANCE",
        "DUE",
        "CHANGE",
        "REF",
        "AUTH",
        "MODE",
        "ISSUER",
        "PIN",
        "VERIFIED",
        "APPROVED",
        "CONTACTLESS",
        "CTLESS",
    )):
        return "payment"
    if text.startswith(("AID", "TVR", "IAD", "TSI", "ARC", "TC", "MID", "SEQ")):
        return "payment"
    return "body"


def _cached_receipt_dict(example: dict) -> dict:
    if example.get("tokens") and example.get("bboxes"):
        return _cached_token_receipt_dict(example)
    return _cached_line_receipt_dict(example)


# Cached-example canvas sizes (W, H), selected from the example's own metadata
# (candidate_id). These are format sizes for the line/token synthetic examples,
# not per-merchant geometry -- the hybrid/grid path sizes itself from the
# receipt's true aspect instead.
_CANVAS_ADDRESS_LINE = (560, 1280)
_CANVAS_DEFAULT = (576, 1176)


def _cached_output_size(example: dict) -> tuple[int, int]:
    candidate_id = str(example.get("candidate_id") or "")
    if "address-line" in candidate_id or "hard_negative" in candidate_id:
        return _CANVAS_ADDRESS_LINE
    return _CANVAS_DEFAULT


def _smooth_luma_field(rng, height, width, scale, amp):
    """A low-frequency [-amp, amp] field: small random grid upsampled (BICUBIC).

    Models slowly-varying luminance -- uneven heat/paper coating (blotches) or,
    with a 1-column grid, horizontal print-head banding.
    """
    import numpy as np
    from PIL import Image

    gh = max(2, height // scale)
    gw = max(1, width // scale)
    small = rng.normal(0.0, 1.0, (gh, gw)).astype(np.float32)
    lo, hi = float(small.min()), float(small.max())
    norm = (small - lo) / (hi - lo + 1e-6)
    img = Image.fromarray((norm * 255.0).astype(np.uint8)).resize(
        (width, height), Image.BICUBIC)
    return (np.asarray(img).astype(np.float32) / 255.0 - 0.5) * 2.0 * amp


def _composite_paper_texture(image, *, seed: int | None = None, strength: float | None = None):
    """Composite thermal-paper realism onto a clean render.

    Layers (all bounded, applied AFTER layout so they never hide a layout bug):
    slight ink-bleed blur, low-frequency heat/paper blotching, horizontal
    print-head banding, a top-to-bottom thermal fade, fine grain, an edge
    vignette, and a fractional-degree skew with paper-colour fill (the scan tilt).
    ``strength`` (env ``RECEIPT_PAPER_STRENGTH``, default 1.0) scales the effect.
    """
    import numpy as np
    from PIL import Image, ImageFilter

    if strength is None:
        try:
            strength = float(os.environ.get("RECEIPT_PAPER_STRENGTH", "1.0"))
        except ValueError:
            strength = 1.0
    s = max(0.0, float(strength))
    source_mode = image.mode
    base = image.convert("RGB")
    # Ink bleed: thermal dots spread slightly -- soften the razor-digital edges.
    if s > 0:
        base = base.filter(ImageFilter.GaussianBlur(0.6 * min(s, 1.5)))
    rgb = np.asarray(base).astype(np.float32)
    height, width, _ = rgb.shape
    rng = np.random.default_rng(seed)

    if s > 0:
        # Low-frequency heat/paper blotching + horizontal print-head banding + a
        # gentle top->bottom fade: multiply luminance by their combined field.
        blotch = _smooth_luma_field(rng, height, width, scale=48, amp=0.05 * s)
        band = _smooth_luma_field(rng, height, 1, scale=16, amp=0.035 * s)
        band = np.repeat(band[:, :1], width, axis=1)
        ys = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
        fade = 1.0 - 0.05 * s * ys  # slightly lighter toward the tail
        lum = (1.0 + blotch + band) * fade
        rgb *= lum[:, :, None]

    # Fine grain (neutral: same delta on R/G/B).
    grain = rng.normal(0.0, 4.5 * s, size=(height, width, 1)).astype(np.float32)
    rgb += grain

    # Edge vignette: darker toward the paper edges/corners.
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float32)[:, None]
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float32)[None, :]
    radial = np.sqrt(xs * xs + ys * ys) / np.sqrt(2.0)
    vignette = (1.0 - 0.07 * s * (radial * radial))[:, :, None]
    rgb *= vignette

    # Scanner-bed cyan BARS down the left/right edges: on a real scan the outer
    # ~6% of each side is a solid cyan band (red channel ~24 below green/blue), with
    # a quick transition to white -- a bar, not a gradient. Measured off the scan.
    if s > 0:
        bar_w, soft = 0.06, 0.025
        xn = np.linspace(0.0, 1.0, width, dtype=np.float32)
        d = np.minimum(xn, 1.0 - xn)                          # 0 at edges -> 0.5 center
        ew = np.clip((bar_w + soft - d) / soft, 0.0, 1.0)[None, :]  # flat 1 in bar
        rgb[..., 0] -= 24.0 * s * ew
        rgb[..., 1] -= 2.0 * s * ew
        rgb[..., 2] -= 4.0 * s * ew

    rgb = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
    textured = Image.fromarray(rgb, mode="RGB")

    # Scan tilt: a fractional-degree rotation, paper-colour fill on the corners.
    if s > 0:
        angle = float(rng.uniform(-0.8, 0.8)) * min(s, 1.5)
        fill = tuple(int(v) for v in np.asarray(image.convert("RGB"))[0, 0])
        textured = textured.rotate(angle, resample=Image.BICUBIC, fillcolor=fill)

    if source_mode == "RGB":
        return textured
    return textured.convert(source_mode)


# ---------------------------------------------------------------------------
# Per-merchant render profiles (data-driven registry).
#
# All per-merchant configuration lives in ``merchant_profiles.json`` (keyed by
# exact merchant_name), NOT in code, so onboarding a merchant is a data edit.
# See docs/synthetic-receipt-rendering-generalization.md. The helpers below read
# that registry; their signatures and return shapes are unchanged, so callers
# (render_matrix, _render_cached_hybrid) are untouched.
# ---------------------------------------------------------------------------

# Header size relative to body when a profile omits section_scale (measured +
# dual-reviewed study of the real receipts; Epson Font A/B shrink). ``{}`` in a
# profile means uniform (no shrink); an absent profile uses this default.
_DEFAULT_SECTION_SCALE = {"HEADER": 0.80}

# Bundled font faces the glyph-prototype matcher converged on (receiptfont.com
# families): VT323 (pixel/dot-matrix, bitMatrix-D1/pixCrog), PT Mono (Epson-style
# bitMatrix-A2), B612 Mono (clean condensed sans). A profile's typography.font is
# one of these tokens; the loader resolves it to the path so profiles carry no
# machine-specific paths.
_PTMONO = "/System/Library/Fonts/Supplemental/PTMono.ttc"
_VENDORED_FONTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "receipt_agent", "receipt_agent",
    "agents", "label_evaluator", "rendering", "fonts",
)
_VT323 = os.path.join(_VENDORED_FONTS_DIR, "VT323-Regular.ttf")   # OFL pixel/dot-matrix
_B612 = os.path.join(_VENDORED_FONTS_DIR, "B612Mono-Regular.ttf")  # OFL clean sans mono
_FONT_TOKENS = {"PTMONO": _PTMONO, "VT323": _VT323, "B612": _B612}
# Glyph atlases + logo PNGs (bitMatrix-C2 chart derived / logo_master medians),
# kept local (paid-font derived). Relocate via $BITMATRIX_DIR.
_BITMATRIX_DIR = os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix")

_MERCHANT_PROFILES_PATH = os.path.join(
    os.path.dirname(__file__), "merchant_profiles.json"
)
_MERCHANT_PROFILES_CACHE: dict | None = None


def load_merchant_profiles(refresh: bool = False) -> dict:
    """Load and cache the merchant profile registry (``merchant_profiles.json``)."""
    global _MERCHANT_PROFILES_CACHE
    if _MERCHANT_PROFILES_CACHE is None or refresh:
        with open(_MERCHANT_PROFILES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        _MERCHANT_PROFILES_CACHE = data.get("profiles", {}) or {}
    return _MERCHANT_PROFILES_CACHE


def get_merchant_profile(merchant: str | None) -> dict:
    """The registry record for ``merchant`` (exact match), or ``{}`` if absent."""
    return load_merchant_profiles().get(merchant or "", {})


def section_scale_for_merchant(merchant: str | None) -> dict:
    """Per-merchant header scale; falls back to the measured default (0.80)."""
    profile = get_merchant_profile(merchant)
    if "section_scale" in profile:
        return dict(profile["section_scale"])
    return dict(_DEFAULT_SECTION_SCALE)


def _merchant_logo(merchant: str | None):
    """Canonical logo for a merchant as RGBA (alpha = ink), or None.

    The logo master (logo_master.py) is a phase-correlated + majority-vote median
    across the merchant's receipts, stored as a black-on-white PNG under
    $BITMATRIX_DIR; here it becomes ink-as-alpha for the overlay. An absent
    ``logo`` field (e.g. Target, whose thermal receipts print no wordmark) or a
    missing file renders the header as text.
    """
    fname = get_merchant_profile(merchant).get("logo")
    path = os.path.join(_BITMATRIX_DIR, fname) if fname else None
    if not path or not os.path.exists(path):
        return None
    import numpy as np
    from PIL import Image
    g = np.asarray(Image.open(path).convert("L")).astype(np.uint8)
    rgba = np.zeros((*g.shape, 4), np.uint8)
    rgba[..., 3] = 255 - g   # dark ink -> opaque, white paper -> transparent
    return Image.fromarray(rgba, "RGBA")


def merchant_typography(merchant: str | None) -> dict:
    """Per-merchant grid typography kwargs; {} -> default grid font.

    Resolves the profile's ``typography`` block: the ``font`` token -> a bundled
    font path, ``bitmap_font`` filenames -> $BITMATRIX_DIR paths, and copies the
    remaining shaping/treatment keys (condense, stroke, display_headings,
    reverse_*, dashed_separators) through verbatim. Drops a bitmap_font/font_path
    whose assets are missing (e.g. CI without the local atlases) so rendering
    falls back gracefully.
    """
    typo = get_merchant_profile(merchant).get("typography", {})
    cfg: dict = {}
    for key, val in typo.items():
        if key.startswith("_"):
            continue  # provenance/_comment
        if key == "font":
            path = _FONT_TOKENS.get(val)
            if path:
                cfg["font_path"] = path
        elif key == "bitmap_font":
            cfg["bitmap_font"] = {
                k: os.path.join(_BITMATRIX_DIR, v) for k, v in val.items()
            }
        else:
            cfg[key] = val
    bf = cfg.get("bitmap_font")
    if bf and not os.path.exists(bf.get("regular", "")):
        cfg.pop("bitmap_font", None)
    if cfg.get("font_path") and not os.path.exists(cfg["font_path"]):
        cfg.pop("font_path", None)
    return cfg


# The glyph atlas + merchant font profile are deterministic per merchant but cost
# ~20 sequential S3/DynamoDB round-trips to build (the dominant render latency).
# Cache them to disk so re-renders after a code edit are near-instant. Set
# RENDER_CACHE_DIR to relocate; pass refresh=True to rebuild.
_RENDER_CACHE_DIR = os.environ.get("RENDER_CACHE_DIR", "/tmp/render_cache")


def _render_cache_path(kind: str, merchant: str | None, n: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", merchant or "none")
    return os.path.join(_RENDER_CACHE_DIR, f"{safe}__{kind}__n{n}.pkl")


def _cached_build(kind, build, table, merchant, region, max_receipts, refresh):
    path = _render_cache_path(kind, merchant, max_receipts)
    if not refresh and os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass  # corrupt/stale cache -> rebuild
    obj = build(table, merchant, region=region, max_receipts=max_receipts)
    if obj is not None:
        try:
            os.makedirs(_RENDER_CACHE_DIR, exist_ok=True)
            with open(path, "wb") as fh:
                pickle.dump(obj, fh)
        except Exception:
            pass  # caching is best-effort
    return obj


def cached_glyph_atlas(table, merchant, *, region, max_receipts=8, refresh=False):
    """Disk-cached :func:`build_glyph_atlas_from_dynamo` (per merchant)."""
    return _cached_build(
        "atlas", build_glyph_atlas_from_dynamo,
        table, merchant, region, max_receipts, refresh,
    )


def cached_font_profile(table, merchant, *, region, max_receipts=12, refresh=False):
    """Disk-cached :func:`build_merchant_font_profile_from_dynamo` (per merchant)."""
    return _cached_build(
        "profile", build_merchant_font_profile_from_dynamo,
        table, merchant, region, max_receipts, refresh,
    )


def resolve_bitmap_thin(table, merchant, *, region, atlas, profile,
                        section_scale=None, typography=None, refresh=False):
    """Derived-by-default glyph erosion (see synthesis_loop/ink_calibration).

    ``bitmap_thin`` is not a per-merchant opinion: the right value is whatever
    makes a re-render of a REAL receipt match that receipt's measured ink
    density. An explicit ``bitmap_thin`` in the merchant profile still wins;
    otherwise render one real receipt at candidate erosions, compare per-word
    ink density against the real scan (scorecard measurers), and bisect.
    Disk-cached like the atlas/profile builds. Calibration renders run under
    the CALLER's ``RECEIPT_PAPER_STRENGTH`` — the texture's ink-bleed blur
    materially fattens thresholded strokes, so calibrating texture-off would
    solve the wrong equation. Run it under the same strength you render with.
    The derived value is the median over a few sample receipts (real scans
    vary in ink darkness).
    """
    typography = dict(typography or {})
    if "bitmap_thin" in typography:
        return float(typography["bitmap_thin"])
    path = _render_cache_path("inkthin", merchant, 1)
    if not refresh and os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass  # corrupt/stale cache -> rebuild

    import tempfile

    from PIL import Image

    loop_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "synthesis_loop"
    )
    if loop_dir not in sys.path:
        sys.path.insert(0, loop_dir)
    from ink_calibration import derive_bitmap_thin  # noqa: E402
    from receipt_line_scorecard import _load_words_and_real  # noqa: E402

    from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402

    from statistics import median

    client = DynamoClient(table_name=table, region=region)
    places, _ = client.get_receipt_places_by_merchant(merchant)
    samples = []
    seen = set()
    for place in places:
        key = (str(place.image_id), int(place.receipt_id))
        if key in seen:
            continue
        seen.add(key)
        try:
            real, words = _load_words_and_real(merchant, key[0], key[1])
        except Exception:  # noqa: BLE001
            continue
        if len(words) >= 40:
            samples.append((real, words))
        if len(samples) >= 3 or len(seen) >= 10:
            break
    if not samples:
        return 0.0

    thins = []
    for real, words in samples:
        wt = 760
        # True aspect is mandatory: normalized coords discard proportions.
        ht = max(1, int(round(wt * real.height / real.width)))
        receipt = {
            "words": [dict(word, labels=[]) for word in words],
            "merchant_name": merchant,
        }

        def render(thin, receipt=receipt, wt=wt, ht=ht):
            typo = dict(typography)
            typo["bitmap_thin"] = float(thin)
            fd, out = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            _render_cached_hybrid(
                receipt, atlas, profile=profile, width=wt, height=ht,
                path=out, section_scale=section_scale, **typo)
            img = Image.open(out).convert("RGB")
            os.unlink(out)
            return img

        derived = derive_bitmap_thin(render, real, words)
        if derived is not None:
            thins.append(float(derived[0]))
    thin = float(median(thins)) if thins else 0.0
    try:
        os.makedirs(_RENDER_CACHE_DIR, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(thin, fh)
    except Exception:
        pass  # caching is best-effort
    return thin


def _render_cached_hybrid(
    receipt: dict,
    atlas,
    *,
    profile,
    width: int,
    height: int,
    path: str,
    font_path: str | None = None,
    section_scale: dict | None = None,
    section_font: dict | None = None,
    condense: float = 1.0,
    stroke: int = 0,
    bitmap_font: dict | None = None,
    display_headings: tuple = (),
    heading_scale: float = 1.0,
    reverse_total: bool = False,
    reverse_date_after_items: bool = False,
    dashed_separators: bool = False,
    heading_bleed_phrase: str | None = None,
    reverse_date_anchor: str | None = None,
    dash_after_amount_date: bool = False,
    mixed_layout: bool = False,
    bitmap_cap_ratio: float = 0.72,
    bitmap_thin: float = 0.0,
    ocr_font_sizing: bool = False,
    ocr_cap_height_ratio: float = 0.72,
    ink: tuple[int, int, int] | list[int] | None = None,
) -> str:
    receipt = _repair_missing_top_header_lines(receipt)
    # Render-time content repair (EMV/auth strings, totals) on the synthetic
    # tokens just before drawing -- fixes the dominant remaining realism tell
    # without re-running synthesis. Mutates the per-render receipt dict in place.
    clean_for_render(receipt)
    config = RenderConfig(
        bitmap_font=bitmap_font,
        width=width,
        height=height,
        margin=10,
        color_by_label=False,
        draw_price_column=False,
        background=(250, 249, 245),
        section_scale=section_scale,
        section_font=section_font,
        condense=condense,
        stroke=stroke,
        display_headings=display_headings,
        heading_scale=heading_scale,
        reverse_total=reverse_total,
        reverse_date_after_items=reverse_date_after_items,
        dashed_separators=dashed_separators,
        heading_bleed_phrase=heading_bleed_phrase,
        reverse_date_anchor=reverse_date_anchor,
        dash_after_amount_date=dash_after_amount_date,
        mixed_layout=mixed_layout,
        bitmap_cap_ratio=bitmap_cap_ratio,
        bitmap_thin=bitmap_thin,
        ocr_font_sizing=ocr_font_sizing,
        ocr_cap_height_ratio=ocr_cap_height_ratio,
        ink=ink,
        # Grid typography (fixed character grid, one body size per receipt, hard
        # non-anti-aliased glyphs on a shared baseline). The merchant profile
        # geometry is the realism control; min/max_font_px are only sanity clamps.
        # The ceiling scales with canvas height so the profile-driven size keeps
        # the real text-to-receipt ratio (~1.5%) at any resolution -- a fixed 28px
        # ceiling shrank the text (and loosened spacing) once the canvas grew.
        min_font_px=9,
        max_font_px=max(28, int(height / 45)),
        grid_mode=True,
        # Optional body-font override. None -> the grid-font candidate list
        # (Andale -> vendored B612 -> legacy). The grid recalibrates cell_w / row
        # pitch from whatever face loads, so the SAME layout renders in any font.
        font_path=font_path,
    )
    # When the atlas supplies a logo bitmap, that bitmap is the source of truth
    # for the merchant wordmark. The MERCHANT_NAME glyph tokens the logo depicts
    # must NOT also be drawn as text, or they double-print into an illegible
    # smear under the pasted logo (e.g. COSTCO's WHOLESALE, SPROUTS' FARMERS
    # MARKET). We suppress those tokens and let the logo depict them.
    #
    # If the captured logo's subtitle band is clipped to an unreadable sliver, we
    # crop it off and instead render the subtitle tokens as ordinary text in
    # their own (reserved) row below the brand wordmark.
    render_input = receipt
    logo_bbox = None
    logo_image = None
    logo_subtitle = None
    # Prefer the canonical (averaged-across-receipts) logo over the smeared atlas
    # capture; it is the full wordmark, so treat it as depicting the subtitle.
    canon_logo = _merchant_logo(receipt.get("merchant_name"))
    _have_logo = canon_logo is not None or (
        atlas is not None and getattr(atlas, "logo", None) is not None)
    if _have_logo:
        if canon_logo is not None:
            logo_image, depicts_subtitle = canon_logo, True
        else:
            logo_image, depicts_subtitle = _trim_clipped_subtitle(atlas.logo)
        # Merchants whose wordmark is a pure graphic (no MERCHANT_NAME OCR text,
        # e.g. The Home Depot) anchor the logo off a configured slogan phrase.
        anchor_cfg = get_merchant_profile(
            receipt.get("merchant_name")).get("logo_anchor") or {}
        placed = None
        if anchor_cfg.get("phrases") and logo_image is not None:
            placed = _phrase_logo_placement(
                receipt, anchor_cfg["phrases"], config=config,
                logo=logo_image, coord_max=1000.0,
                extend_left=anchor_cfg.get("extend_left", True),
            )
        if placed is not None:
            drop_words, logo_bbox = placed
            render_input = _receipt_drop_words(receipt, drop_words)
        else:
            logo_line = _cached_logo_line(receipt)
            if logo_line:
                if depicts_subtitle:
                    # Logo shows the whole wordmark: suppress brand + subtitle
                    # text and size the logo to the full wordmark region.
                    wordmark = _logo_wordmark_words(receipt)
                    if wordmark:
                        wordmark_words, logo_bbox = wordmark
                        if (
                            get_merchant_profile(
                                receipt.get("merchant_name")
                            ).get("logo_reserve_subtitle")
                            and len(wordmark_words) == len(logo_line)
                        ):
                            logo_bbox = _reserve_logo_subtitle_bbox(logo_bbox)
                        render_input = _receipt_drop_words(
                            receipt, wordmark_words)
                else:
                    # Logo shows only the brand line. For merchants whose
                    # subtitle is part of the wordmark (Sprouts' FARMERS
                    # MARKET), synthesize the subtitle inside the logo overlay
                    # instead of routing it through receipt text layout.
                    merchant_profile = get_merchant_profile(
                        receipt.get("merchant_name"))
                    subtitle = merchant_profile.get("logo_subtitle")
                    wordmark = _logo_wordmark_words(receipt)
                    if subtitle:
                        wordmark_words, logo_bbox = (
                            wordmark if wordmark is not None else (
                                logo_line,
                                _union_bbox([
                                    w["bbox"] for w in logo_line
                                    if w.get("bbox")
                                ]),
                            )
                        )
                        if (wordmark is None
                                or len(wordmark_words) == len(logo_line)):
                            logo_bbox = _reserve_logo_subtitle_bbox(logo_bbox)
                        logo_subtitle = str(subtitle)
                        render_input = _receipt_drop_words(
                            receipt, wordmark_words)
                    else:
                        logo_bbox = _union_bbox(
                            [w["bbox"] for w in logo_line if w.get("bbox")]
                        )
                        render_input = _receipt_drop_words(
                            receipt, logo_line)
    image = render_receipt(
        render_input,
        profile=profile,
        config=config,
        coord_max=1000.0,
    ).convert("RGBA")
    _overlay_cached_logo(
        image,
        receipt,
        atlas,
        config=config,
        coord_max=1000.0,
        bbox=logo_bbox,
        logo_image=logo_image,
        subtitle_text=logo_subtitle,
    )
    stamped_bands = _overlay_detected_codes(
        image, receipt, config=config, coord_max=1000.0
    )
    if not stamped_bands:
        stamped_bands = _overlay_inbody_barcodes(
            image, receipt, config=config, coord_max=1000.0
        )
    _overlay_qr_and_barcode(
        image, receipt, config=config, coord_max=1000.0, reserved=stamped_bands
    )
    # Deterministic per-output seed so re-rendering the same file is stable.
    texture_seed = zlib.crc32(os.path.basename(path).encode("utf-8"))
    image = _composite_paper_texture(image, seed=texture_seed)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.convert("RGB").save(path, format="PNG")
    return path


def _reserve_logo_subtitle_bbox(bbox: list[float]) -> list[float]:
    """Reserve a subtitle band when OCR only saw the top logo word."""
    x0, y0, x1, y1 = bbox
    low, high = sorted((float(y0), float(y1)))
    height = max(1.0, high - low)
    return [float(x0), max(0.0, low - height * 0.50), float(x1), high]


def _overlay_cached_logo(
    image,
    receipt: dict,
    atlas,
    *,
    config: RenderConfig,
    coord_max: float,
    bbox: list[float] | None = None,
    logo_image=None,
    subtitle_text: str | None = None,
) -> None:
    # ``logo_image`` (e.g. a clipped-subtitle-trimmed copy) overrides the atlas
    # bitmap when supplied.
    logo = logo_image if logo_image is not None else getattr(atlas, "logo", None)
    if logo is None:
        return
    # ``bbox`` (the full wordmark region, including any reserved subtitle row)
    # may be supplied by the caller; otherwise fall back to the brand line.
    if bbox is None:
        logo_line = _cached_logo_line(receipt)
        if not logo_line:
            return
        bbox = _union_bbox(
            [word["bbox"] for word in logo_line if word.get("bbox")]
        )
    if bbox is None:
        return
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    left, top, right, bottom = _to_pixel_box(
        bbox,
        coord_max=coord_max,
        margin=config.margin,
        inner_w=inner_w,
        inner_h=inner_h,
    )
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    brand_h = box_h * (0.70 if subtitle_text else 1.0)
    scale = min(box_w / logo.width, brand_h / logo.height)
    if scale <= 0:
        return
    size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
    # Raise contrast so the wordmark reads as near-black thermal ink instead of
    # the faint light-gray crop straight off the aged paper photo.
    scaled = _darken_logo(logo).resize(size)
    x = int(left + (box_w - size[0]) / 2)
    y = int(top + (brand_h - size[1]) / 2)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    pad = 3
    draw.rectangle(
        [left - pad, top - pad, right + pad, bottom + pad],
        fill=config.background + (255,),
    )
    image.alpha_composite(scaled, (max(0, x), max(0, y)))
    if subtitle_text:
        _draw_logo_subtitle(
            image,
            subtitle_text,
            left,
            top + brand_h * 0.78,
            right,
            bottom,
        )


_LOGO_SUBTITLE_FONTS = (
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
)


def _draw_logo_subtitle(image, text: str, left: float, top: float, right: float, bottom: float) -> None:
    from PIL import Image, ImageDraw, ImageFont

    label = str(text or "").strip().upper()
    if not label:
        return
    max_w = max(1, int((right - left) * 0.72))
    max_h = max(1, int(bottom - top))
    font = None
    for size in range(max_h, 5, -1):
        for path in _LOGO_SUBTITLE_FONTS:
            if not os.path.exists(path):
                continue
            try:
                candidate = ImageFont.truetype(path, size)
            except OSError:
                continue
            bbox = candidate.getbbox(label)
            if bbox[2] - bbox[0] <= max_w and bbox[3] - bbox[1] <= max_h:
                font = candidate
                break
        if font is not None:
            break
    if font is None:
        font = ImageFont.load_default()
    bbox = font.getbbox(label)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = 4
    mask = Image.new("L", (text_w + 2 * pad, text_h + 2 * pad), 0)
    draw = ImageDraw.Draw(mask)
    draw.text((pad - bbox[0], pad - bbox[1]), label, font=font, fill=255)
    mask = mask.point(lambda value: int(round(value * 0.84)))
    ink = Image.new("RGBA", mask.size, (38, 36, 34, 0))
    ink.putalpha(mask)
    x = int((left + right - mask.width) / 2.0)
    y = int(top + max(0.0, (bottom - top - mask.height) / 2.0))
    image.alpha_composite(ink, (max(0, x), max(0, y)))


def _darken_logo(logo):
    """Return a higher-contrast copy of the captured wordmark.

    The captured logo already carries near-black ink (RGB ~0) but a *faint*
    alpha channel (mean opacity ~30/255), so composited over paper it reads as
    light-gray. We lift the existing alpha with a gamma curve so the strokes go
    opaque near-black, and force the ink RGB to a thermal near-black. The alpha
    mask (not luminance) is what segments ink from background, so we keep it.
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(logo.convert("RGBA")).astype(np.float32)
    alpha = arr[..., 3] / 255.0
    # Gamma < 1 lifts faint strokes toward opaque; the modest gain finishes it.
    boosted = np.clip(np.power(alpha, 0.45) * 1.35, 0.0, 1.0)
    out = np.zeros_like(arr, dtype=np.uint8)
    out[..., 0] = 20
    out[..., 1] = 20
    out[..., 2] = 20
    out[..., 3] = (boosted * 255.0).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


# A captured subtitle band shorter than this fraction of the brand wordmark's
# height is treated as a clipped sliver (letter-tops only) that reads as a smear
# when pasted, and is cropped off the logo. Costco's WHOLESALE band (~0.20) is
# kept; Sprouts' clipped FARMERS MARKET (~0.13) is dropped.
_CLIPPED_SUBTITLE_RATIO = 0.16


def _trim_clipped_subtitle(logo):
    """Drop a clipped partial subtitle band fused to the bottom edge of a logo.

    Some captured wordmark crops slice through the subtitle line (e.g. the
    ``FARMERS MARKET`` band under SPROUTS is cut to letter-tops). Pasted as-is
    that sliver reads as an illegible smear beneath the brand. We detect a thin
    ink band that runs into the bottom edge and is separated from the main
    wordmark by a horizontal whitespace gap, then crop it off.

    Returns ``(logo, depicts_subtitle)``. ``depicts_subtitle`` is ``False`` when
    a band was trimmed, so the caller knows the logo no longer shows the subtitle
    and should render those tokens as ordinary text in their own row instead.
    A full, legible subtitle band (e.g. Costco's WHOLESALE) is left in place and
    reported as depicted.
    """
    import numpy as np

    arr = np.asarray(logo.convert("RGBA")).astype(np.float32)
    alpha = arr[..., 3] / 255.0
    height = alpha.shape[0]
    if height < 16:
        return logo, True
    row_ink = alpha.mean(axis=1)
    peak = float(row_ink.max())
    if peak <= 0.0:
        return logo, True
    ink = row_ink > 0.12 * peak
    # The subtitle is only "clipped" if ink runs right into the bottom edge.
    if not bool(ink[-1]):
        return logo, True
    # Walk up across the contiguous bottom ink band.
    y = height - 1
    while y >= 0 and ink[y]:
        y -= 1
    band_top = y + 1
    band_h = height - band_top
    # Require a whitespace gap separating the band from the wordmark above it.
    gap = 0
    while y >= 0 and not ink[y]:
        y -= 1
        gap += 1
    if gap < 1 or y < 0:
        return logo, True
    # ``y`` now indexes the last ink row of the main wordmark (its baseline).
    main_bottom = y + 1
    # Height of the main wordmark (top-most ink row down to the gap).
    top = 0
    while top < height and not ink[top]:
        top += 1
    main_h = main_bottom - top
    if main_h <= 0:
        return logo, True
    if band_h <= _CLIPPED_SUBTITLE_RATIO * main_h:
        # Crop at the wordmark baseline so the whitespace gap and the clipped
        # subtitle sliver (including its sub-threshold letter-tops) are dropped.
        return logo.crop((0, 0, logo.width, main_bottom)), False
    return logo, True


def _iter_receipt_bboxes(receipt: dict) -> list[list[float]]:
    boxes: list[list[float]] = []
    words = receipt.get("words")
    if not words:
        words = [
            word
            for line in (receipt.get("lines") or [])
            for word in (line.get("words") or [])
        ]
    for word in words or []:
        bbox = word.get("bbox")
        if bbox and len(bbox) >= 4:
            boxes.append([float(v) for v in bbox[:4]])
    return boxes


def _qr_payload(receipt: dict, seed: int) -> str:
    """Deterministic, plausible QR payload (a short receipt-lookup URL)."""
    merchant = (receipt.get("merchant_name") or "store").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "", merchant) or "store"
    token = f"{seed & 0xFFFFFFFF:08x}"
    return f"https://r.{slug[:18]}.example/t/{token}"


def _barcode_payload(kind: str, seed: int) -> str:
    """Deterministic, plausible 1D barcode payload for ``kind``."""
    rng = __import__("random").Random(seed)
    if kind == "upca":
        return "".join(str(rng.randint(0, 9)) for _ in range(11))
    # Code128 transaction barcode: a long numeric transaction id.
    return "".join(str(rng.randint(0, 9)) for _ in range(18))


def _paste_graphic_tile(image, tile, x: int, y: int) -> None:
    """Paste a grayscale code tile into the (RGBA) receipt at (x, y).

    The tile is opaque paper-toned grayscale, so it replaces the blank band it
    lands in. It is pasted *before* the paper-texture pass so it ages with the
    rest of the print instead of looking like a clean sticker on top.
    """
    image.paste(tile.convert("RGBA"), (int(x), int(y)))


def _fit_1d_barcode_tile_to_box(tile, w_px: int, h_px: int):
    """Resize the visible 1D barcode ink to the requested geometry.

    ``python-barcode`` returns an image with quiet-zone/paper padding. That is
    correct for standalone barcode generation, but detected receipt barcode
    boxes usually bound the printed bars themselves. Crop to dark ink before the
    final resize so the bar height comes from Dynamo/OCR geometry instead of the
    library's internal page margins.
    """
    from PIL import Image

    w_px = max(1, int(w_px))
    h_px = max(1, int(h_px))
    gray = tile.convert("L")
    ink_mask = gray.point(lambda p: 255 if p < 230 else 0)
    ink_box = ink_mask.getbbox()
    if ink_box is not None:
        gray = gray.crop(ink_box)
    if gray.size != (w_px, h_px):
        gray = gray.resize((w_px, h_px), Image.NEAREST)
    return gray


def _hri_digits(text: str) -> str | None:
    """The digit string if ``text`` is a long human-readable barcode caption."""
    digits = re.sub(r"[^0-9]", "", str(text or ""))
    raw = re.sub(r"\s", "", str(text or ""))
    # >=14 digits and almost entirely numeric (allow a few separators).
    if len(digits) >= 14 and len(digits) >= 0.8 * len(raw):
        return digits
    return None


def _detected_codes(receipt: dict) -> list:
    for key in ("barcodes", "receipt_barcodes", "codes"):
        value = receipt.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def _field(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _code_bbox(code, coord_max: float) -> list[float] | None:
    bbox = _field(code, "bbox")
    if bbox is None:
        bbox = _field(code, "bounding_box")
    if isinstance(bbox, dict):
        try:
            x = float(bbox["x"])
            y = float(bbox["y"])
            w = float(bbox["width"])
            h = float(bbox["height"])
        except (KeyError, TypeError, ValueError):
            bbox = None
        else:
            bbox = [x, y, x + w, y + h]
    if bbox is None:
        tl = _field(code, "top_left")
        br = _field(code, "bottom_right")
        if isinstance(tl, dict) and isinstance(br, dict):
            try:
                bbox = [
                    float(tl["x"]), float(tl["y"]),
                    float(br["x"]), float(br["y"]),
                ]
            except (KeyError, TypeError, ValueError):
                return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        out = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if max(abs(v) for v in out) <= 1.5:
        out = [v * coord_max for v in out]
    return out


def _code_payload(code, fallback: str) -> str:
    for name in ("payload", "text", "data", "value"):
        value = _field(code, name)
        if value:
            return str(value)
    return fallback


def _code_symbology(code) -> str:
    return str(_field(code, "symbology", "") or "").lower()


def _code_is_qr(code) -> bool:
    sym = _code_symbology(code)
    return "qr" in sym or "aztec" in sym or "datamatrix" in sym


def _barcode_kind_for_code(code, merchant: str | None) -> str:
    sym = _code_symbology(code)
    if "upc" in sym or "ean" in sym:
        return "upca"
    if "code128" in sym or "code_128" in sym:
        return "code128"
    return graphics_for_merchant(merchant)["barcode_kind"]


def _overlay_detected_codes(
    image, receipt: dict, *, config: RenderConfig, coord_max: float
) -> list[tuple[float, float]]:
    """Paste generated codes into OCR-detected boxes from PR #1028.

    The OCR barcode entity gives us the geometry real receipts used. When present
    that is better than choosing the largest blank band heuristically.
    """
    codes = _detected_codes(receipt)
    if not codes:
        return []
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    boxes = _iter_receipt_bboxes(receipt)
    seed = zlib.crc32(("".join(str(b) for b in boxes[:8])).encode("utf-8"))
    bands: list[tuple[float, float]] = []
    for index, code in enumerate(codes):
        bbox = _code_bbox(code, coord_max)
        if bbox is None:
            continue
        left, top, right, bottom = _to_pixel_box(
            bbox, coord_max=coord_max, margin=config.margin,
            inner_w=inner_w, inner_h=inner_h,
        )
        left, right = sorted((left, right))
        top, bottom = sorted((top, bottom))
        w = max(1, int(right - left))
        h = max(1, int(bottom - top))
        if w < 20 or h < 20:
            continue
        if _code_is_qr(code):
            size = min(w, h)
            tile = receipt_graphics.render_qr_tile(
                _code_payload(code, _qr_payload(receipt, seed ^ index)),
                size,
                seed ^ index,
            )
            x = int(left + (w - size) / 2)
            y = int(top + (h - size) / 2)
        else:
            kind = _barcode_kind_for_code(code, receipt.get("merchant_name"))
            tile = receipt_graphics.render_barcode_tile(
                _code_payload(code, _barcode_payload(kind, seed ^ index)),
                kind,
                w,
                h,
                with_hri=False,
            )
            tile = _fit_1d_barcode_tile_to_box(tile, w, h)
            x, y = int(left), int(top)
        _paste_graphic_tile(image, tile, x, y)
        bands.append((float(top), float(bottom)))
    return bands


# In-body transaction barcode geometry (stamped above a long-numeric HRI line).
# A merchant profile's graphics.inbody_barcode block overrides any of these.
_INBODY_BARCODE_DEFAULTS = {
    "symbology": "code128",
    "max_count": 2,
    "min_gap_px": 34,
    "bar_h_px": 84,
    "bar_w_frac": 0.60,
    "max_digits": 24,
}


def _visual_barcode_payload(digits: str, symbology: str) -> str:
    """Fallback-only payload shaping for barcode density.

    Detected PR #1028 barcode entities render their real payload unchanged. The
    in-body fallback only sees the printed HRI digits, and python-barcode encodes
    all-numeric Code128 very compactly (Code C), which makes bars too coarse when
    stretched to a receipt-sized box. Pair separators keep a valid Code128 symbol
    while producing the finer module density seen on the Sprouts receipt.
    """
    if str(symbology).lower() != "code128":
        return digits
    pairs = [digits[i:i + 2] for i in range(0, len(digits), 2)]
    return "-".join(pair for pair in pairs if pair)


def graphics_for_merchant(merchant: str | None) -> dict:
    """Merchant graphics choices: the substring-default profile from
    receipt_graphics (barcode symbology / QR), overlaid with any explicit
    ``graphics`` block in the merchant registry so a profile can pin
    barcode_kind / barcode_with_hri / qr and add an inbody_barcode override."""
    base = dict(receipt_graphics.graphics_profile_for_merchant(merchant))
    base.update(get_merchant_profile(merchant).get("graphics", {}) or {})
    return base


def _overlay_inbody_barcodes(
    image, receipt: dict, *, config: RenderConfig, coord_max: float
) -> list[tuple[float, float]]:
    """Stamp Code-128 bars above in-body transaction-number lines (the HRI digits).

    Real receipts print the long transaction number AS a barcode; our text render
    shows only the digits. For each long-numeric line with genuine blank space
    directly above it, paste a bar tile in that gap (never over existing text).
    Returns the stamped ``(y_top, y_bottom)`` pixel bands so the footer QR/barcode
    pass can treat them as occupied and not stack a second barcode in the same gap
    (real Costco has exactly one transaction barcode here, not two).
    """
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    all_words = receipt.get("words") or [
        w for line in (receipt.get("lines") or [])
        for w in (line.get("words") or [])
    ]
    words = [w for w in all_words if w.get("bbox")]
    if not words:
        return 0
    ib = {**_INBODY_BARCODE_DEFAULTS,
          **(graphics_for_merchant(receipt.get("merchant_name")).get(
              "inbody_barcode") or {})}
    # A profile may disable the in-body transaction barcode (max_count 0) for
    # merchants that print the transaction number as plain text, not a barcode
    # (e.g. The Home Depot; its only barcode is the footer).
    if ib.get("max_count", 0) <= 0:
        return []
    px = []
    for w in words:
        l, t, r, b = _to_pixel_box(
            w["bbox"], coord_max=coord_max, margin=config.margin,
            inner_w=inner_w, inner_h=inner_h,
        )
        px.append((w, min(t, b), max(t, b), min(l, r), max(l, r)))
    stamped_bands: list[tuple[float, float]] = []
    for i, (w, top, bot, left, right) in enumerate(px):
        digits = _hri_digits(w.get("text"))
        if digits is None:
            continue
        # nearest content bottom strictly above this line
        above = [pb for j, (_, pt, pb, _, _) in enumerate(px)
                 if j != i and pb <= top + 2]
        nearest = max(above) if above else float(config.margin)
        space = top - nearest
        if space < ib["min_gap_px"]:
            continue
        bar_h = int(min(ib["bar_h_px"], space - 8))
        bar_w = int(min(
            inner_w * 0.72,
            max((right - left) * 1.30, inner_w * float(ib["bar_w_frac"])),
        ))
        cx = (left + right) / 2.0
        payload = _visual_barcode_payload(digits[:ib["max_digits"]], ib["symbology"])
        tile = receipt_graphics.render_barcode_tile(
            payload, ib["symbology"], bar_w, bar_h, with_hri=False
        )
        tile = _fit_1d_barcode_tile_to_box(tile, bar_w, bar_h)
        y_top = int(top - 6 - bar_h)
        _paste_graphic_tile(image, tile, int(cx - bar_w / 2), y_top)
        stamped_bands.append((float(y_top), float(top - 6)))
        if len(stamped_bands) >= ib["max_count"]:
            break
    return stamped_bands


def _overlay_qr_and_barcode(
    image, receipt: dict, *, config: RenderConfig, coord_max: float,
    reserved: list[tuple[float, float]] | None = None,
) -> None:
    """Stamp a REAL QR symbol and a REAL 1D barcode in the blank footer region.

    The renderer's footer narration ("Scan the QR code") promised anchors that
    were never drawn (the old pass faked them with random modules / random
    bars + a stray digit caption). We now generate genuine, scannable codes via
    :mod:`receipt_graphics` (segno + python-barcode) and paste them into a real
    blank band keyed off the receipt so re-renders are stable. The barcode
    symbology is chosen per merchant; the human-readable caption is omitted to
    match real receipt footers (the spurious digits were a realism tell).
    """
    # If the receipt already prints its transaction number as an in-body barcode
    # (Costco et al.), a footer QR/barcode block is redundant and real prints omit
    # it -- stamping one anyway drops a stray barcode into whatever blank band
    # exists (e.g. up by the header). Skip the footer graphic in that case.
    if reserved:
        return
    boxes = _iter_receipt_bboxes(receipt)
    if not boxes:
        return
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    paper_top = float(config.margin)
    paper_bottom = float(config.height - config.margin)

    # Occupied vertical intervals in pixel space (boxes are y-high-is-top).
    intervals: list[tuple[float, float]] = []
    for bbox in boxes:
        _, t, _, b = _to_pixel_box(
            bbox,
            coord_max=coord_max,
            margin=config.margin,
            inner_w=inner_w,
            inner_h=inner_h,
        )
        intervals.append((min(t, b), max(t, b)))
    # Treat already-stamped in-body barcode bands as occupied so we don't stack a
    # redundant second barcode in the same gap.
    for r0, r1 in (reserved or []):
        intervals.append((min(r0, r1), max(r0, r1)))
    intervals.sort()
    merged: list[list[float]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    # Blank vertical bands between printed content.
    gaps: list[tuple[float, float]] = []
    prev = paper_top
    for s, e in merged:
        if s - prev > 0:
            gaps.append((prev, s))
        prev = max(prev, e)
    if paper_bottom - prev > 0:
        gaps.append((prev, paper_bottom))
    if not gaps:
        return
    # Stamp anchors only in genuine whitespace so we never occlude real tokens
    # (costco/sprouts fill to the bottom edge — a blind footer stamp clobbers the
    # DATE/TIME line). Use the tallest blank band.
    pad = 8.0
    gtop, gbot = max(gaps, key=lambda g: g[1] - g[0])
    gtop += pad
    gbot -= pad
    avail_h = gbot - gtop
    if avail_h < 60:
        return

    seed = zlib.crc32(("".join(str(b) for b in boxes[:8])).encode("utf-8"))
    cx = config.margin + inner_w / 2.0
    bar_w = int(inner_w * 0.6)
    bar_h = 46
    gap = 24
    qr_size = min(120, int(inner_w * 0.32))
    block_full = qr_size + gap + bar_h

    gfx = graphics_for_merchant(receipt.get("merchant_name"))
    kind = gfx["barcode_kind"]
    with_hri = gfx["barcode_with_hri"]
    barcode_tile = receipt_graphics.render_barcode_tile(
        _barcode_payload(kind, seed ^ 0x5A5A),
        kind,
        bar_w,
        bar_h,
        with_hri=with_hri,
    )

    if avail_h >= block_full:
        y0 = int(gtop + (avail_h - block_full) / 2)
        qr_tile = receipt_graphics.render_qr_tile(
            _qr_payload(receipt, seed), qr_size, seed
        )
        _paste_graphic_tile(image, qr_tile, int(cx - qr_size / 2), y0)
        _paste_graphic_tile(
            image, barcode_tile, int(cx - bar_w / 2), y0 + qr_size + gap
        )
    elif avail_h >= 64 + gap + bar_h:
        # Band too short for a full QR block: fit a SMALLER QR + barcode rather
        # than dropping the QR entirely (the footer narration promises one).
        qs = min(qr_size, int(avail_h - gap - bar_h))
        block = qs + gap + bar_h
        y0 = int(gtop + (avail_h - block) / 2)
        qr_tile = receipt_graphics.render_qr_tile(
            _qr_payload(receipt, seed), qs, seed
        )
        _paste_graphic_tile(image, qr_tile, int(cx - qs / 2), y0)
        _paste_graphic_tile(image, barcode_tile, int(cx - bar_w / 2), y0 + qs + gap)
    elif avail_h >= bar_h + 6:
        y0 = int(gtop + (avail_h - bar_h) / 2)
        _paste_graphic_tile(image, barcode_tile, int(cx - bar_w / 2), y0)


def _cached_logo_line(receipt: dict) -> list[dict] | None:
    lines = receipt.get("lines")
    if not lines:
        words = receipt.get("words") or []
        grouped: dict[int, list[dict]] = {}
        for word in words:
            bbox = word.get("bbox")
            if not bbox:
                continue
            y = round((float(bbox[1]) + float(bbox[3])) / 20) * 10
            grouped.setdefault(int(y), []).append(word)
        lines = [{"words": line_words} for line_words in grouped.values()]

    candidates = []
    for line in lines:
        line_words = [word for word in line.get("words", []) if word.get("bbox")]
        if not line_words:
            continue
        labels = {
            _label_name(label)
            for word in line_words
            for label in (word.get("labels") or [])
        }
        text = " ".join(str(word.get("text") or "") for word in line_words)
        normalized = "".join(ch for ch in text.upper() if ch.isalnum())
        if "MERCHANT_NAME" not in labels and "SPROUTS" not in normalized:
            continue
        score = 0
        if normalized.startswith("SPROUTS"):
            score += 3
        if "SPROUTS" in normalized:
            score += 2
        if "MERCHANT_NAME" in labels:
            score += 1
        y = max(float(word["bbox"][3]) for word in line_words)
        candidates.append((score, y, line_words))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _receipt_lines(receipt: dict) -> list[list[dict]]:
    """Group the receipt's words into rendered lines (shared with logo-line
    detection): use explicit ``lines`` if present, else band words by y."""
    lines = receipt.get("lines")
    if lines:
        return [
            [w for w in line.get("words", []) if w.get("bbox")] for line in lines
        ]
    grouped: dict[int, list[dict]] = {}
    for word in receipt.get("words") or []:
        bbox = word.get("bbox")
        if not bbox:
            continue
        y = round((float(bbox[1]) + float(bbox[3])) / 20) * 10
        grouped.setdefault(int(y), []).append(word)
    return list(grouped.values())


def _phrase_logo_placement(receipt, phrases, *, config, logo, coord_max,
                           extend_left=True):
    """Place a top-of-receipt logo lockup that has NO ``MERCHANT_NAME`` anchor.

    Some merchants print the wordmark as a pure graphic (no OCR text) beside a
    slogan (e.g. The Home Depot's tilted square + "How doers get more done."). The
    ``MERCHANT_NAME``/brand logo-line detection finds nothing, so we anchor off
    the slogan instead: any line whose alphanumeric-normalized text contains one
    of the profile ``phrases`` is part of the lockup. Those words are suppressed
    (the logo depicts them) and the logo is sized to a band matching its own pixel
    aspect ratio, optionally extended left to the margin to cover the graphic mark
    that sits left of the slogan. Returns (drop_words, bbox) or ``None``.
    """
    norm = [_normalize_phrase(p) for p in phrases if p]
    if not norm:
        return None
    drop, boxes = [], []
    for words in _receipt_lines(receipt):
        text = _normalize_phrase(" ".join(str(w.get("text") or "") for w in words))
        if not text or not any(p in text for p in norm):
            continue
        drop.extend(words)
        boxes.extend(w["bbox"] for w in words if w.get("bbox"))
    band = _union_bbox(boxes)
    if band is None:
        return None
    bx0, bx1 = min(band[0], band[2]), max(band[0], band[2])
    by0, by1 = min(band[1], band[3]), max(band[1], band[3])
    if extend_left:
        bx0 = 0.0
    # Size the band to the logo's pixel aspect so the wordmark fills its footprint
    # exactly (no over-wide white-out erasing neighbouring rows).
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    aspect = logo.width / max(1, logo.height)
    w_px = (bx1 - bx0) / coord_max * inner_w
    h_coords = (w_px / max(1e-6, aspect)) / max(1, inner_h) * coord_max
    yc = (by0 + by1) / 2.0
    return drop, [bx0, yc - h_coords / 2.0, bx1, yc + h_coords / 2.0]


def _normalize_phrase(text: str) -> str:
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def _flatten_receipt_words(receipt: dict) -> list[dict]:
    """Return the receipt's word dicts (originals, for identity matching)."""
    words = receipt.get("words")
    if words:
        return list(words)
    return [
        word
        for line in (receipt.get("lines") or [])
        for word in (line.get("words") or [])
    ]


def _logo_wordmark_words(receipt: dict) -> tuple[list[dict], list[float]] | None:
    """The MERCHANT_NAME wordmark that the atlas logo bitmap depicts.

    Returns the detected logo line plus any MERCHANT_NAME subtitle word(s)
    contiguous with it (e.g. COSTCO + WHOLESALE, SPROUTS + FARMERS MARKET) and
    the union bbox of the whole wordmark. These text tokens are suppressed from
    the glyph render and depicted by the pasted logo bitmap instead, so the
    wordmark never double-prints as a smear under the logo. The union bbox also
    reserves the subtitle's row for the logo so the wordmark is sized to the full
    region (not squashed into the single brand line).

    Generalizes off the existing logo-line / MERCHANT_NAME detection; no
    per-merchant hardcoding.
    """
    logo_line = _cached_logo_line(receipt)
    if not logo_line:
        return None
    band = _union_bbox([word["bbox"] for word in logo_line if word.get("bbox")])
    if band is None:
        return None
    cluster = list(logo_line)
    cluster_ids = {id(word) for word in cluster}
    # OCR bboxes may be bottom-origin (y0 > y1), so read spans via min/max rather
    # than assuming [1] is the top -- a raw ``band[3] - band[1]`` goes negative on
    # inverted coords and collapses the contiguity gap, leaking the subtitle
    # (e.g. COSTCO's WHOLESALE) as double-printed text under the logo.
    def _yspan(b):
        return min(float(b[1]), float(b[3])), max(float(b[1]), float(b[3]))

    def _xspan(b):
        return min(float(b[0]), float(b[2])), max(float(b[0]), float(b[2]))

    by0, by1 = _yspan(band)
    bx0, bx1 = _xspan(band)
    line_h = max(1.0, by1 - by0)
    # Only rows immediately adjacent to the brand line (within one line height)
    # count as the logo's subtitle, so far-away MERCHANT_NAME tokens (e.g. a
    # footer ".com" wordmark) are not absorbed.
    gap = line_h
    candidates = [
        word
        for word in _flatten_receipt_words(receipt)
        if id(word) not in cluster_ids
        and word.get("bbox")
        and any(
            _label_name(label) == "MERCHANT_NAME"
            for label in (word.get("labels") or [])
        )
    ]
    changed = True
    while changed:
        changed = False
        for word in candidates:
            if id(word) in cluster_ids:
                continue
            wx0, wx1 = _xspan(word["bbox"])
            wy0, wy1 = _yspan(word["bbox"])
            # Vertical contiguity with the current wordmark band.
            if wy0 > by1 + gap or wy1 < by0 - gap:
                continue
            # Must sit in the same horizontal column as the wordmark.
            if wx1 <= bx0 or wx0 >= bx1:
                continue
            cluster.append(word)
            cluster_ids.add(id(word))
            by0, by1 = min(by0, wy0), max(by1, wy1)
            bx0, bx1 = min(bx0, wx0), max(bx1, wx1)
            changed = True
    return cluster, [bx0, by0, bx1, by1]


def _receipt_drop_words(receipt: dict, drop: list[dict]) -> dict:
    """Shallow copy of ``receipt`` with ``drop`` word dicts removed.

    The original word dicts are left untouched so callers that still need the
    full receipt (e.g. logo placement) keep working.
    """
    drop_ids = {id(word) for word in drop}
    new = dict(receipt)
    if receipt.get("words") is not None:
        new["words"] = [
            word for word in receipt["words"] if id(word) not in drop_ids
        ]
    if receipt.get("lines") is not None:
        new_lines = []
        for line in receipt["lines"]:
            new_line = dict(line)
            new_line["words"] = [
                word
                for word in (line.get("words") or [])
                if id(word) not in drop_ids
            ]
            new_lines.append(new_line)
        new["lines"] = new_lines
    return new


def _union_bbox(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _to_pixel_box(
    bbox: list[float],
    *,
    coord_max: float,
    margin: int,
    inner_w: int,
    inner_h: int,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(value) for value in bbox[:4])
    left_x, right_x = sorted((x0, x1))
    bottom_y, top_y = sorted((y0, y1))
    return (
        margin + (left_x / coord_max) * inner_w,
        margin + ((coord_max - top_y) / coord_max) * inner_h,
        margin + (right_x / coord_max) * inner_w,
        margin + ((coord_max - bottom_y) / coord_max) * inner_h,
    )


def _render_cached_synthetic_examples(args: argparse.Namespace) -> int:
    table_name = args.dynamodb_table_name or os.environ.get(
        "DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"
    )
    merchant = args.merchant
    atlas = build_glyph_atlas_from_dynamo(
        table_name,
        merchant,
        region=args.aws_region,
        max_receipts=args.glyph_max_receipts,
    )
    if atlas is None:
        print(f"No glyph atlas available for {merchant}.")
        return 1
    profile = build_merchant_font_profile_from_dynamo(
        table_name,
        merchant,
        region=args.aws_region,
        max_receipts=args.profile_max_receipts,
    )
    fallback = make_ttf_fallback(atlas)

    os.makedirs(args.out_dir, exist_ok=True)
    if args.public_dir:
        os.makedirs(args.public_dir, exist_ok=True)

    rendered = 0
    for path in sorted(
        name
        for name in os.listdir(args.cached_synthetic_dir)
        if name.endswith(".json")
    ):
        source_path = os.path.join(args.cached_synthetic_dir, path)
        example = json.load(open(source_path, encoding="utf-8"))
        # The batch merchant is authoritative; make it available to the receipt
        # builders (header dedup/reflow keys off the merchant profile).
        example.setdefault("merchant_name", args.merchant)
        receipt = _cached_receipt_dict(example)
        width, height = _cached_output_size(example)
        out_name = f"{os.path.splitext(path)[0]}.png"
        out_path = os.path.join(args.out_dir, out_name)
        if args.cached_renderer == "hybrid":
            _render_cached_hybrid(
                receipt,
                atlas,
                profile=profile,
                width=width,
                height=height,
                path=out_path,
            )
        else:
            config = GlyphRenderConfig(
                width=width,
                height=height,
                seed=args.seed + rendered,
                noise=args.noise,
                blur=args.blur,
                paper_realism=args.paper_realism,
                body_glyph_source=args.body_glyph_source,
            )
            save_receipt_glyphs(
                receipt,
                atlas,
                out_path,
                profile=profile,
                config=config,
                coord_max=1000.0,
                fallback=fallback,
            )
        if args.public_dir:
            public_path = os.path.join(args.public_dir, out_name)
            if args.cached_renderer == "hybrid":
                _render_cached_hybrid(
                    receipt,
                    atlas,
                    profile=profile,
                    width=width,
                    height=height,
                    path=public_path,
                )
            else:
                save_receipt_glyphs(
                    receipt,
                    atlas,
                    public_path,
                    profile=profile,
                    config=config,
                    coord_max=1000.0,
                    fallback=fallback,
                )
            print("wrote", public_path)
        else:
            print("wrote", out_path)
        rendered += 1

    print(
        f"Rendered {rendered} cached synthetic candidate(s) with a "
        f"{atlas.receipt_count}-receipt glyph atlas."
    )
    return 0


def _profile_from_export_dir(merchant: str, receipt_dir: str):
    receipt_profiles = []
    for name in sorted(os.listdir(receipt_dir)):
        if not name.endswith(".json"):
            continue
        export = json.load(open(os.path.join(receipt_dir, name)))
        # Group words / lines / letters per receipt id.
        words_by_rid: dict[int, list] = {}
        lines_by_rid: dict[int, list] = {}
        letters_by_rid: dict[int, list] = {}
        for word in export.get("receipt_words", []) or []:
            words_by_rid.setdefault(word.get("receipt_id"), []).append(word)
        for line in export.get("receipt_lines", []) or []:
            lines_by_rid.setdefault(line.get("receipt_id"), []).append(line)
        for letter in export.get("receipt_letters", []) or []:
            letters_by_rid.setdefault(letter.get("receipt_id"), []).append(letter)
        for rid, words in words_by_rid.items():
            profile = extract_receipt_font_profile(
                words,
                lines_by_rid.get(rid),
                letters=letters_by_rid.get(rid),
            )
            if profile is not None:
                receipt_profiles.append(profile)
    return build_merchant_font_profile(merchant, receipt_profiles)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle")
    parser.add_argument("--receipt-dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--merchant", default="Vons")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument(
        "--cached-synthetic-dir",
        help="Render cached synthetic JSON examples instead of a replay bundle.",
    )
    parser.add_argument(
        "--public-dir",
        help="Optional public asset directory to mirror cached PNG outputs into.",
    )
    parser.add_argument(
        "--dynamodb-table-name",
        help="DynamoDB table for glyph-atlas/profile construction.",
    )
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--glyph-max-receipts", type=int, default=8)
    parser.add_argument("--profile-max-receipts", type=int, default=12)
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument("--noise", type=float, default=0.42)
    parser.add_argument("--blur", type=float, default=0.35)
    parser.add_argument("--paper-realism", type=float, default=0.65)
    parser.add_argument(
        "--body-glyph-source",
        choices=("atlas", "numeric", "font"),
        default="numeric",
        help="Glyph source policy for body text in cached glyph renders.",
    )
    parser.add_argument(
        "--cached-renderer",
        choices=("hybrid", "glyph"),
        default="hybrid",
        help="Hybrid keeps body text legible and overlays the atlas logo.",
    )
    args = parser.parse_args()

    if args.cached_synthetic_dir:
        return _render_cached_synthetic_examples(args)
    if not args.bundle or not args.receipt_dir:
        parser.error("--bundle and --receipt-dir are required outside cached mode")

    bundle = json.load(open(args.bundle))
    examples = bundle.get("synthetic_training_examples", []) or []
    if not examples:
        print("No synthetic_training_examples in bundle.")
        return 1

    # Map ner_tag ids back to label names if the bundle records them.
    id_to_label: dict[int, str] = {}
    policy = bundle.get("synthetic_training_batch_policy") or {}
    for key in ("id_to_label", "label_list", "labels"):
        value = policy.get(key)
        if isinstance(value, dict):
            id_to_label = {int(k): v for k, v in value.items()}
            break
        if isinstance(value, list):
            id_to_label = {i: lbl for i, lbl in enumerate(value)}
            break

    # Index exported files by image id for base-receipt lookup.
    exports: dict[str, dict] = {}
    for name in os.listdir(args.receipt_dir):
        if name.endswith(".json"):
            exports[name[:-5]] = json.load(
                open(os.path.join(args.receipt_dir, name))
            )

    profile = _profile_from_export_dir(args.merchant, args.receipt_dir)
    print("Merchant profile:", json.dumps(profile.to_dict() if profile else None))

    os.makedirs(args.out_dir, exist_ok=True)
    config = RenderConfig(width=460, height=1100, color_by_label=True,
                          draw_price_column=True)

    rendered = 0
    for example in examples[: args.limit]:
        candidate_id = example.get("candidate_id", f"candidate-{rendered}")
        base_key = (example.get("metadata") or {}).get("base_receipt_key", "")
        image_id, _, suffix = base_key.partition("#")
        try:
            base_receipt_id = int(suffix)
        except ValueError:
            base_receipt_id = None

        synthetic = _synthetic_receipt_dict(example, id_to_label)
        synth_path = os.path.join(args.out_dir, f"{candidate_id}.synthetic.png")
        save_receipt_png(synthetic, synth_path, profile=profile, config=config)

        if image_id in exports and base_receipt_id is not None:
            real = _real_receipt_dict(exports[image_id], base_receipt_id)
            combined = render_real_vs_synthetic(
                real, synthetic, profile=profile, config=config,
                labels=(f"real {base_key}", f"synthetic {example.get('operation','')}")
            )
            combined_path = os.path.join(
                args.out_dir, f"{candidate_id}.real_vs_synthetic.png"
            )
            combined.save(combined_path)
            print("wrote", combined_path)
        else:
            print("wrote", synth_path, "(no base receipt found for", base_key, ")")
        rendered += 1

    print(f"Rendered {rendered} candidate(s) to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
