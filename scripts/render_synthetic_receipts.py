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
import random
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


_CACHED_AMOUNT_RE = re.compile(
    r"^(?:USD)?\$?\d{1,4}(?:,\d{3})*\.\d{2}[A-Z]?-?$",
    re.IGNORECASE,
)
_CACHED_BARCODE_RE = re.compile(r"^\d{14,}$")
_CACHED_WEEKDAY_TIMESTAMP_RE = re.compile(
    r"^(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)"
)
_CACHED_CURRENCY_MARKERS = {"USD$", "$", "USD"}
_CACHED_AMOUNT_LABELS = {
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
    "UNIT_PRICE",
    "AMOUNT",
    "BALANCE",
    "TOTAL",
    "PRICE",
}
_CACHED_LINE_LEFT_X = 70.0
_CACHED_PRICE_RIGHT_X = 835.0
_CACHED_PRICE_GAP_X = 24.0
_CACHED_QR_MODULES = 29
_CACHED_LOGO_WIDTH = 280.0
_CACHED_LOGO_HALF_HEIGHT = 30.0
_CACHED_WORD_MIN_WIDTH = 22.0
_CACHED_CHAR_WIDTH = 16.0
_CACHED_BODY_HALF_HEIGHT = 8.0
_CACHED_LOGO_SUBTITLE_GAP = 38.0
_CACHED_SECTION_GAP = 10.0
_CACHED_MAX_LINE_SPACING = 14.0
_CACHED_SPARSE_MAX_LINE_SPACING = 19.0
_CACHED_VERY_SPARSE_MAX_LINE_SPACING = 22.0
_CACHED_MAX_FONT_PX = 15
_CACHED_QR_SIZE_FACTOR = 0.27
_CACHED_QR_MIN_SIZE = 128.0
_CACHED_QR_MAX_SIZE = 160.0
_CACHED_QR_TOP_FACTOR = 0.70
_CACHED_QR_FOOTER_TAIL_START_Y = 92.0
_CACHED_QR_FOOTER_TAIL_BOTTOM_Y = 18.0
_CACHED_ARITHMETIC_OUTPUT_SIZE = (352, 1176)
_CACHED_ADDRESS_OUTPUT_SIZE = (416, 1280)
_CACHED_THERMAL_DARK_SPECKLE_RATE = 0.064
_CACHED_THERMAL_LIGHT_SPECKLE_RATE = 0.066
_CACHED_THERMAL_MIN_DARK_DENSITY = 0.105
_CACHED_THERMAL_SCANLINE_MIN_GAP = 42
_CACHED_THERMAL_SCANLINE_MAX_GAP = 74
_CACHED_THERMAL_MOTTLE_COUNT_FACTOR = 90
_CACHED_THERMAL_WARMTH_R = 2
_CACHED_THERMAL_WARMTH_G = 1
_CACHED_THERMAL_WARMTH_B = -3
_CACHED_SPROUTS_FRAGMENT_TEXTS = {
    "TO",
    "TH",
    "USED",
    "PM",
    "CARD",
    "K",
    "AUTHREF",
    "WENEEDYOURCHAN",
}


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
    return {"words": words}


def _cached_token_receipt_dict(example: dict) -> dict:
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
    words = _drop_duplicate_sprouts_header_words(words)
    if any("SPROUTS" in _compact_line_text(line) for line in _group_cached_words_by_line(words)):
        return _line_receipt_from_cached_token_words(
            words,
            candidate_id=str(example.get("candidate_id") or ""),
            example=example,
        )
    return {"words": words}


def _cached_line_receipt_dict(example: dict) -> dict:
    lines = []
    source_lines = _order_cached_sprouts_lines(
        _ensure_sprouts_farmers_market_header_line(
            _drop_duplicate_sprouts_header_lines(example)
        )
    )
    for index, line in enumerate(source_lines):
        text = _normalize_cached_sprouts_line_text(
            str(line.get("text") or "").strip()
        )
        if not text:
            continue
        words = text.split()
        y = float(line.get("y") or (940 - index * 16))
        labels = list(line.get("labels") or [])
        compact_text = _compact_text(text)
        is_logo_line = (
            any(_label_name(label) == "MERCHANT_NAME" for label in labels)
            and compact_text.startswith("SPROUTS")
        )
        is_centered_header = _is_centered_sprouts_header_text(text)
        amount_start = None if is_logo_line else _cached_amount_cluster_start(words)
        is_barcode_line = _is_cached_barcode_text(text)
        # Cached line-only examples do not carry OCR word widths, so give the
        # renderer enough horizontal room to avoid shrinking every line to the
        # minimum font size.
        width_units = [
            max(_CACHED_WORD_MIN_WIDTH, len(word) * _CACHED_CHAR_WIDTH)
            for word in words
        ]
        if is_logo_line and len(words) == 1:
            width_units = [max(width_units[0], _CACHED_LOGO_WIDTH)]
        if is_barcode_line and len(width_units) == 1:
            width_units = [max(width_units[0], 560.0)]
        total_width = sum(width_units) + max(0, len(words) - 1) * 8.0
        if total_width > 900:
            factor = 900 / total_width
            width_units = [width * factor for width in width_units]
            total_width = 900
        if is_logo_line:
            x = 500 - total_width / 2
        elif is_centered_header:
            x = 500 - total_width / 2
        elif is_barcode_line:
            x = 500 - total_width / 2
        else:
            x = _CACHED_LINE_LEFT_X
        half_height = (
            _CACHED_LOGO_HALF_HEIGHT if is_logo_line else _CACHED_BODY_HALF_HEIGHT
        )
        rendered_words = []
        amount_x = None
        if amount_start is not None:
            amount_width = (
                sum(width_units[amount_start:])
                + max(0, len(width_units) - amount_start - 1) * 8.0
            )
            amount_x = _CACHED_PRICE_RIGHT_X - amount_width
            body_width = (
                sum(width_units[:amount_start])
                + max(0, amount_start - 1) * 8.0
            )
            available_body = amount_x - _CACHED_PRICE_GAP_X - _CACHED_LINE_LEFT_X
            if amount_start and body_width > available_body > 0:
                factor = available_body / body_width
                width_units[:amount_start] = [
                    max(_CACHED_WORD_MIN_WIDTH, width * factor)
                    for width in width_units[:amount_start]
                ]
        for word_index, (word, width) in enumerate(zip(words, width_units)):
            if amount_x is not None and word_index == amount_start:
                x = amount_x
            is_amount_word = amount_start is not None and word_index >= amount_start
            rendered_words.append(
                {
                    "text": word,
                    "bbox": [x, y - half_height, x + width, y + half_height],
                    "labels": _cached_word_labels(labels, is_amount_word),
                }
            )
            x += width + 8
        lines.append({"line_id": index + 1, "words": rendered_words})
    return {"lines": lines}


def _ensure_sprouts_farmers_market_header_line(lines: list[dict]) -> list[dict]:
    if not any(
        _compact_text(line.get("text") or "").startswith("SPROUTS")
        for line in lines
    ):
        return lines
    if any(_compact_text(line.get("text") or "") == "FARMERSMARKET" for line in lines):
        return lines

    inserted = []
    for line in lines:
        text = _compact_text(line.get("text") or "")
        if text.startswith("SPROUTS") and "FARMERSMARKET" in text:
            brand_line = dict(line)
            brand_line["text"] = "SPROUTS"
            inserted.append(brand_line)
            inserted.append(
                {
                    "text": "FARMERS MARKET",
                    "y": None,
                    "labels": ["MERCHANT_NAME"],
                }
            )
            continue
        inserted.append(line)
        if text == "SPROUTS":
            inserted.append(
                {
                    "text": "FARMERS MARKET",
                    "y": None,
                    "labels": ["MERCHANT_NAME"],
                }
            )
    return inserted


def _is_centered_sprouts_header_text(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    return _is_sprouts_header_line(compact) or compact == "LOCALFAVORITES"


def _cached_amount_cluster_start(words: list[str]) -> int | None:
    if not words:
        return None
    last_index = len(words) - 1
    if not _is_cached_amount_token(words[last_index]):
        return None
    start = last_index
    if start > 0 and _is_cached_currency_marker(words[start - 1]):
        start -= 1
    return start


def _is_cached_amount_token(token: str) -> bool:
    return bool(_CACHED_AMOUNT_RE.match(token.strip()))


def _is_cached_currency_marker(token: str) -> bool:
    return token.strip().upper() in _CACHED_CURRENCY_MARKERS


def _is_cached_barcode_text(text: str) -> bool:
    tokens = [token.strip() for token in str(text or "").split() if token.strip()]
    if not tokens or not all(token.isdigit() for token in tokens):
        return False
    return bool(_CACHED_BARCODE_RE.match("".join(tokens)))


def _cached_word_labels(labels: list[str], is_amount_word: bool) -> list[str]:
    if is_amount_word:
        return labels
    return [
        label
        for label in labels
        if _label_name(label) not in _CACHED_AMOUNT_LABELS
    ]


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


def _drop_duplicate_sprouts_header_lines(example: dict) -> list[dict]:
    """Keep one Sprouts header/address block in cached line-only renders."""
    seen: set[str] = set()
    kept = []
    for line in example.get("lines") or []:
        text = _compact_text(str(line.get("text") or ""))
        if _is_sprouts_header_line(text):
            if text in seen:
                continue
            seen.add(text)
        kept.append(line)
    return kept


def _order_cached_sprouts_lines(lines: list[dict]) -> list[dict]:
    if not any("SPROUTS" in _compact_text(line.get("text") or "") for line in lines):
        return lines
    has_feedback_url = any(
        "SPROUTSFEEDBACK" in _compact_text(line.get("text") or "")
        for line in lines
    )
    lines = [
        line for line in lines
        if not _drop_cached_sprouts_fragment_line(
            line,
            has_feedback_url=has_feedback_url,
        )
    ]

    sections: dict[str, list[dict]] = {
        "header": [],
        "body": [],
        "payment": [],
        "footer": [],
    }
    for line in lines:
        sections[_sprouts_text_section(_compact_text(line.get("text") or ""))].append(line)

    ordered = []
    for name in ("header", "body", "payment", "footer"):
        if ordered and sections[name]:
            ordered.append({"text": "", "y": None, "labels": []})
        for line in sections[name]:
            if name == "footer" and _is_cached_barcode_text(line.get("text") or ""):
                ordered.append({"text": "", "y": None, "labels": []})
            ordered.append(line)

    real_count = sum(1 for line in ordered if str(line.get("text") or "").strip())
    break_count = len(ordered) - real_count
    section_gap = _CACHED_SECTION_GAP
    max_spacing = _cached_sprouts_max_line_spacing(real_count)
    spacing = (930.0 - break_count * section_gap) / max(1, real_count - 1)
    spacing = max(9.0, min(max_spacing, spacing))

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
        line_gap = (
            _CACHED_LOGO_SUBTITLE_GAP
            if _compact_text(text).startswith("SPROUTS")
            else spacing
        )
        y -= line_gap
    return positioned


def _cached_sprouts_max_line_spacing(real_count: int) -> float:
    if 35 <= real_count <= 42:
        return _CACHED_VERY_SPARSE_MAX_LINE_SPACING
    if 35 <= real_count <= 52:
        return _CACHED_SPARSE_MAX_LINE_SPACING
    return _CACHED_MAX_LINE_SPACING


def _drop_cached_sprouts_fragment_line(
    line: dict,
    *,
    has_feedback_url: bool = False,
) -> bool:
    """Drop OCR leftovers that should be part of larger Sprouts footer lines."""
    raw_text = str(line.get("text") or "").strip()
    compact = _compact_text(raw_text)
    if not compact and "*" in raw_text:
        return True
    if not compact:
        return False
    if "REWARDSPROGRAM" in compact or "PLEASEPLEASE" in compact:
        return True
    if has_feedback_url and compact == "FEEDBACK":
        return True
    if compact in _CACHED_SPROUTS_FRAGMENT_TEXTS:
        return True
    if compact == "62566Z317081":
        return True
    if (
        compact.startswith("TAKEAQUICKSURVEYENTERFORTHE")
        and "CHANCE" not in compact
    ):
        return True
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}", raw_text):
        return True
    if re.fullmatch(r"\d{1,3}\s+\d{3}", raw_text):
        return True
    if re.fullmatch(r"\d{5,6}[A-Z]?", compact) and raw_text.rstrip().endswith(("-", "—")):
        return True
    return False


def _drop_duplicate_sprouts_header_words(words: list[dict]) -> list[dict]:
    """Remove repeated Sprouts header/address blocks from cached examples."""
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
        if text in {"PRODUCE", "DAIRY", "GROCERY"}:
            product_y = max(product_y or y, y)

    seen: set[str] = set()
    drop_ids: set[int] = set()
    for y in sorted(line_groups, reverse=True):
        line_words = line_groups[y]
        text = _compact_line_text(line_words)
        if product_y is not None and y <= product_y:
            continue
        if not _is_sprouts_header_line(text):
            continue
        if text in seen:
            drop_ids.update(id(word) for word in line_words)
            continue
        seen.add(text)

    return [word for word in words if id(word) not in drop_ids]


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


def _is_sprouts_header_line(text: str) -> bool:
    is_brand_line = text.startswith("SPROUTS") and not any(
        marker in text for marker in ("FEEDBACK", "COM", "GIFT")
    )
    return (
        is_brand_line
        or text in {"FARMERSMARKET"}
        or "WESTLAKE" in text
        or text in {"8059174200", "STOREHOURSMONSUN7AM10PM"}
    )


def _line_receipt_from_cached_token_words(
    words: list[dict],
    *,
    candidate_id: str = "",
    example: dict | None = None,
) -> dict:
    """Convert cached token boxes into a legible public line-render receipt."""
    ordered = _ordered_sprouts_token_lines(words)
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
    lines = _normalize_cached_arithmetic_sprouts_lines(
        lines,
        candidate_id=candidate_id,
        example=example or {},
    )
    lines = _reconstruct_sparse_sprouts_remove_item_lines(
        lines,
        candidate_id=candidate_id,
    )
    return _cached_line_receipt_dict({"lines": lines})


def _normalize_cached_arithmetic_sprouts_lines(
    lines: list[dict],
    *,
    candidate_id: str,
    example: dict,
) -> list[dict]:
    if "sprouts-arithmetic" not in candidate_id:
        return lines

    normalized = [
        line
        for line in lines
        if not _drop_cached_arithmetic_sprouts_line(line)
    ]
    if not _should_insert_cached_sprouts_summary(normalized):
        return normalized

    metadata = example.get("metadata") or {}
    arithmetic = metadata.get("arithmetic_reconciliation") or {}
    total = (
        arithmetic.get("new_grand_total")
        or metadata.get("new_grand_total")
        or example.get("new_grand_total")
    )
    if not total:
        return normalized
    subtotal = (
        arithmetic.get("new_subtotal")
        or metadata.get("new_subtotal")
        or total
    )
    tax = (
        arithmetic.get("new_tax")
        or metadata.get("new_tax")
        or arithmetic.get("tax_delta")
        or metadata.get("tax_delta")
        or "0.00"
    )
    item_count = _cached_sprouts_item_count(metadata)
    summary_lines = [
        {"text": f"SUBTOTAL {subtotal}", "y": None, "labels": ["SUBTOTAL"]},
        {"text": f"TAX {tax}", "y": None, "labels": ["TAX"]},
    ]
    if item_count is not None:
        summary_lines.append(
            {
                "text": f"NO. OF ITEMS SOLD {item_count}",
                "y": None,
                "labels": [],
            }
        )

    insert_at = _cached_sprouts_summary_insert_index(normalized)
    return [*normalized[:insert_at], *summary_lines, *normalized[insert_at:]]


def _drop_cached_arithmetic_sprouts_line(line: dict) -> bool:
    compact = _compact_text(line.get("text") or "")
    return compact == "13FOR500"


def _should_insert_cached_sprouts_summary(lines: list[dict]) -> bool:
    texts = {_compact_text(line.get("text") or "") for line in lines}
    return not any(text.startswith("SUBTOTAL") for text in texts)


def _cached_sprouts_item_count(metadata: dict) -> int | None:
    retained_count = metadata.get("retained_line_item_count")
    if isinstance(retained_count, int) and retained_count > 0:
        return retained_count
    signature = (
        (metadata.get("structure_similarity") or {})
        .get("candidate_signature")
        or {}
    )
    signature_count = signature.get("line_item_count")
    if isinstance(signature_count, int) and signature_count > 0:
        return signature_count
    return None


def _cached_sprouts_summary_insert_index(lines: list[dict]) -> int:
    for index, line in enumerate(lines):
        if _sprouts_text_section(_compact_text(line.get("text") or "")) == "payment":
            return index
    return len(lines)


def _reconstruct_sparse_sprouts_remove_item_lines(
    lines: list[dict],
    *,
    candidate_id: str,
) -> list[dict]:
    """Rebuild the sparse Sprouts remove-item scan from OCR fragments."""
    if "remove-line-item" not in candidate_id:
        return lines

    compact_all = " ".join(_compact_text(line.get("text") or "") for line in lines)
    required_markers = (
        "SPROUTS",
        "GREENBEANS349",
        "XXXXXXXXXXXX5061",
        "SPROUTSFEEDBACKCOM",
    )
    if not all(marker in compact_all for marker in required_markers):
        return lines

    amount = _sprouts_remove_item_amount(lines) or "3.49"
    card_number = _sprouts_remove_item_card_number(lines) or "XXXXXXXXXXXX5061"
    auth_code, ref_number = _sprouts_remove_item_auth_ref(lines)
    barcode = _sprouts_remove_item_barcode(lines) or "19022003126062"
    timestamp = _sprouts_remove_item_timestamp(lines)

    payment_lines = [
        "MASTERCARD Entry Method:Cntctless",
        f"CARD #: {card_number}",
        "PURCHASE APPROVED",
    ]
    if auth_code:
        payment_lines.append(f"AUTH CODE: {auth_code}")
    payment_lines.extend(
        [
            "Mode: Issuer",
            *_sprouts_remove_item_card_detail_lines(lines),
            f"BALANCE DUE {amount}",
            f"CREDIT ${amount}",
        ]
    )
    if auth_code and ref_number:
        payment_lines.append(f"Auth# {auth_code} Ref# {ref_number}")
    payment_lines.append("CHANGE 0.00")

    reconstructed = [
        "SPROUTS",
        "FARMERS MARKET",
        "1012 WESTLAKE BLVD.",
        "WESTLAKE, CA 91361",
        "(805) 917-4200",
        "Store Hours MON-SUN 7AM-10PM",
        "PRODUCE",
        f"GREEN BEANS {amount}",
        *payment_lines,
        "We need your feedback!",
        "Take a quick survey & enter for the chance",
        "to WIN a $250 Sprouts gift card. Go to:",
        "SproutsFeedback.com",
        "*5 Winners Monthly*",
        barcode,
        "Cashier:SSCO 31 Store: 220",
        "POS:031 Transaction:2806",
        timestamp,
        "Save money, save paper",
        "sign up to receive our weekly ad",
        "by email at Sprouts.com",
        "Please keep your original receipt, the",
        "type of credit given is determined by",
        "the method of payment used.",
        "ID is required for returns without a",
        "receipt. Limits apply to returns",
        "without a receipt.",
    ]
    return [
        {
            "text": text,
            "y": None,
            "labels": (
                ["MERCHANT_NAME"]
                if _compact_text(text) in {"SPROUTS", "FARMERSMARKET"}
                else []
            ),
        }
        for text in reconstructed
    ]


def _sprouts_remove_item_amount(lines: list[dict]) -> str | None:
    for line in lines:
        text = str(line.get("text") or "")
        match = re.search(r"\bGREEN\s+BEANS\s+(\d+\.\d{2})\b", text, re.IGNORECASE)
        if match:
            return match.group(1)
    for line in lines:
        text = str(line.get("text") or "")
        match = re.search(r"\bDUE\s+(\d+\.\d{2})\b", text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _sprouts_remove_item_card_number(lines: list[dict]) -> str | None:
    for line in lines:
        text = str(line.get("text") or "")
        match = re.search(r"X{6,}\d{4}", text, re.IGNORECASE)
        if match:
            return match.group(0).upper()
    return None


def _sprouts_remove_item_auth_ref(lines: list[dict]) -> tuple[str | None, str | None]:
    for line in lines:
        text = str(line.get("text") or "")
        match = re.search(r"\b([0-9A-Z]{6})\s+(\d{6})\b", text, re.IGNORECASE)
        if match:
            return match.group(1).upper(), match.group(2)
    auth_code = None
    for line in lines:
        text = str(line.get("text") or "")
        match = re.search(r"\b(\d{5}[A-Z])\b", text, re.IGNORECASE)
        if match:
            auth_code = match.group(1).upper()
            break
    return auth_code, None


def _sprouts_remove_item_barcode(lines: list[dict]) -> str | None:
    for line in lines:
        text = str(line.get("text") or "")
        tokens = [token for token in text.split() if token.isdigit()]
        if tokens:
            digits = "".join(tokens)
            if _CACHED_BARCODE_RE.match(digits):
                return digits
    return None


def _sprouts_remove_item_timestamp(lines: list[dict]) -> str:
    for line in lines:
        text = str(line.get("text") or "")
        if _compact_text(text).startswith("TUESDAYJULY30"):
            text = text.replace("—", "PM").replace(" -", " PM")
            text = re.sub(r"\s+", " ", text).strip()
            if "PM" not in text:
                text = f"{text} PM"
            return text
    return "Tuesday, July 30, 2024 07:35 PM"


def _sprouts_remove_item_card_detail_lines(lines: list[dict]) -> list[str]:
    details: list[str] = []
    for prefix in ("AID:", "TVR:", "IAD:", "TSI:"):
        detail = _sprouts_remove_item_prefixed_line(lines, prefix)
        if detail:
            details.append(detail)
    if any(_compact_text(line.get("text") or "") == "00OFF" for line in lines):
        details.append("ARC: 00")
    return details


def _sprouts_remove_item_prefixed_line(
    lines: list[dict],
    prefix: str,
) -> str | None:
    compact_prefix = _compact_text(prefix)
    for line in lines:
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if _compact_text(text).startswith(compact_prefix) and text != prefix:
            return text
    return None


def _ordered_sprouts_token_lines(words: list[dict]) -> list[tuple[list[dict], bool]]:
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
        sections[_sprouts_token_section(line)].append(line)

    ordered: list[tuple[list[dict], bool]] = []
    for name in ("header", "body", "payment", "footer"):
        if not sections[name]:
            continue
        if ordered:
            ordered.append(([], True))
        ordered.extend((line, False) for line in sections[name])
    return ordered


def _line_text_from_cached_words(line: list[dict]) -> str:
    text = " ".join(
        str(word.get("text") or "").strip()
        for word in sorted(line, key=lambda item: float(item["bbox"][0]))
        if str(word.get("text") or "").strip()
    )
    return _normalize_cached_sprouts_line_text(text)


def _normalize_cached_sprouts_line_text(text: str) -> str:
    normalized = re.sub(r"\$2[Bb]0\b", "$250", text)
    normalized = re.sub(r"\b2/06/2025\b", "12/06/2025", normalized)
    normalized = re.sub(
        r"\bEntry\s+Method:\s*(?:Ontctless|Ontotless|Contactless|Cntctless)\b",
        "Entry Method:Cntctless",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bMode:\s*Issuer\s*-\s*PIN\s+Verified\b",
        "Mode: Issuer-PIN Verified",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\boriginal\s+recei\s+pt,\s*th\b",
        "original receipt, the",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\boriginal\s+receipt,\s*th\b",
        "original receipt, the",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"^(Save\s+money,\s+save\s+paper)\s*[-—]\s*$",
        r"\1",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


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


def _sprouts_token_section(line: list[dict]) -> str:
    text = _compact_line_text(line)
    return _sprouts_text_section(text)


def _sprouts_text_section(text: str) -> str:
    if _is_sprouts_header_line(text):
        return "header"
    if _CACHED_BARCODE_RE.match(text):
        return "footer"
    if text.startswith(("CHANGE", "XXXXXXXXXXXX", "1XXXXXXXXXXXX")):
        return "payment"
    if text in {"00OFF"}:
        return "payment"
    if _CACHED_WEEKDAY_TIMESTAMP_RE.match(text) or re.fullmatch(r"\d{8}\d{6}", text):
        return "footer"
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
        "YOURCHAN",
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
        "SUBTOTAL",
        "TAX",
        "NOOFITEMSSOLD",
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
        receipt = _cached_token_receipt_dict(example)
    else:
        receipt = _cached_line_receipt_dict(example)
    if _cached_should_draw_qr(example):
        return _reflow_cached_qr_footer(receipt)
    return receipt


def _cached_output_size(example: dict) -> tuple[int, int]:
    candidate_id = str(example.get("candidate_id") or "")
    if "address-line" in candidate_id or "hard_negative" in candidate_id:
        return _CACHED_ADDRESS_OUTPUT_SIZE
    return _CACHED_ARITHMETIC_OUTPUT_SIZE


def _cached_should_draw_qr(example: dict) -> bool:
    candidate_id = str(example.get("candidate_id") or "").lower()
    if "add-line-item" not in candidate_id:
        return False
    tokens = " ".join(str(token) for token in (example.get("tokens") or []))
    lines = " ".join(str(line.get("text") or "") for line in (example.get("lines") or []))
    text = _compact_text(f"{tokens} {lines}")
    return "SPROUTSFEEDBACKCOM" in text


def _reflow_cached_qr_footer(receipt: dict) -> dict:
    lines = receipt.get("lines") or []
    if not lines:
        return receipt

    feedback_index = None
    winners_index = None
    for index, line in enumerate(lines):
        text = _cached_render_line_text(line)
        if text == "SPROUTSFEEDBACKCOM":
            feedback_index = index
        if feedback_index is not None and (
            "WINNERS" in text or "WINNER" in text or "MONTHLY" in text
        ):
            winners_index = index
            break
    anchor_index = winners_index if winners_index is not None else feedback_index
    if anchor_index is None or anchor_index >= len(lines) - 1:
        return receipt

    footer_tail = [
        line for line in lines[anchor_index + 1 :]
        if not _drop_cached_qr_footer_line(line)
    ]
    if not footer_tail:
        return {**receipt, "lines": lines[: anchor_index + 1]}

    start_y = _CACHED_QR_FOOTER_TAIL_START_Y
    bottom_y = _CACHED_QR_FOOTER_TAIL_BOTTOM_Y
    spacing = (start_y - bottom_y) / max(1, len(footer_tail) - 1)
    spacing = max(8.0, min(11.0, spacing))
    reflowed_tail = [
        _move_cached_line_y(line, start_y - index * spacing)
        for index, line in enumerate(footer_tail)
    ]
    return {**receipt, "lines": [*lines[: anchor_index + 1], *reflowed_tail]}


def _cached_render_line_text(line: dict) -> str:
    return _compact_line_text(
        [word for word in line.get("words", []) if word.get("bbox")]
    )


def _drop_cached_qr_footer_line(line: dict) -> bool:
    text = _cached_render_line_text(line)
    return "REWARDSPROGRAM" in text or "PLEASEPLEASE" in text


def _move_cached_line_y(line: dict, center_y: float) -> dict:
    moved_words = []
    for word in line.get("words", []) or []:
        bbox = word.get("bbox")
        if not bbox:
            moved_words.append(word)
            continue
        old_center = (float(bbox[1]) + float(bbox[3])) / 2
        offset = center_y - old_center
        moved = dict(word)
        moved["bbox"] = [
            float(bbox[0]),
            float(bbox[1]) + offset,
            float(bbox[2]),
            float(bbox[3]) + offset,
        ]
        moved_words.append(moved)
    return {**line, "words": moved_words}


def _render_cached_hybrid(
    receipt: dict,
    atlas,
    *,
    profile,
    width: int,
    height: int,
    path: str,
    draw_qr_code: bool = False,
) -> str:
    config = RenderConfig(
        width=width,
        height=height,
        margin=10,
        color_by_label=False,
        draw_price_column=False,
        background=(250, 249, 245),
        min_font_px=6,
        max_font_px=_CACHED_MAX_FONT_PX,
        font_path="/System/Library/Fonts/Supplemental/Andale Mono.ttf",
        right_align_amounts=True,
    )
    image = render_receipt(
        receipt,
        profile=profile,
        config=config,
        coord_max=1000.0,
    ).convert("RGBA")
    _overlay_cached_logo(image, receipt, atlas, config=config, coord_max=1000.0)
    _overlay_cached_barcodes(image, receipt, config=config, coord_max=1000.0)
    if draw_qr_code:
        _overlay_cached_qr_code(image, receipt, config=config, coord_max=1000.0)
    _apply_cached_thermal_texture(image, receipt)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.convert("RGB").save(path, format="PNG")
    return path


def _apply_cached_thermal_texture(image, receipt: dict) -> None:
    """Add deterministic scanner grain to paper pixels only."""
    seed = (
        _cached_qr_seed(receipt)
        ^ ((int(image.width) & 0xFFFF) << 16)
        ^ (int(image.height) & 0xFFFF)
    )
    rng = random.Random(seed)
    _apply_cached_thermal_ink_contrast(image)
    _apply_cached_thermal_ink_spread(
        image,
        random.Random(seed ^ 0x4B8C_2F11),
    )
    pixels = image.load()
    row_bias = _cached_thermal_row_biases(image.height, rng)
    for y in range(image.height):
        bias = row_bias[y]
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a < 255 or not _is_cached_paper_pixel(r, g, b):
                continue
            roll = rng.random()
            if roll < _CACHED_THERMAL_DARK_SPECKLE_RATE:
                gray = rng.randint(138, 166)
                r1, g1, b1 = _cached_thermal_warm_rgb(gray)
                pixels[x, y] = (
                    _clamp_cached_channel(r1 + rng.randint(-2, 2)),
                    _clamp_cached_channel(g1 + rng.randint(-2, 2)),
                    _clamp_cached_channel(b1 + rng.randint(-1, 3)),
                    a,
                )
            elif roll < (
                _CACHED_THERMAL_DARK_SPECKLE_RATE
                + _CACHED_THERMAL_LIGHT_SPECKLE_RATE
            ):
                gray = min(245, max(184, ((r + g + b) // 3) + bias + rng.randint(-14, 2)))
                pixels[x, y] = (*_cached_thermal_warm_rgb(gray), a)
            elif bias:
                gray = min(252, max(220, ((r + g + b) // 3) + bias))
                pixels[x, y] = (*_cached_thermal_warm_rgb(gray), a)
    _apply_cached_thermal_mottle(
        image,
        random.Random(seed ^ 0x7B21_53D9),
    )
    _apply_cached_thermal_scanline_banding(
        image,
        random.Random(seed ^ 0xA5C3_1F27),
    )
    _apply_cached_thermal_density_floor(
        image,
        random.Random(seed ^ 0x9D37_442B),
        min_density=_CACHED_THERMAL_MIN_DARK_DENSITY,
    )


def _cached_thermal_row_biases(height: int, rng: random.Random) -> list[int]:
    biases = [0] * max(0, height)
    for start in range(0, height, 18):
        bias = rng.randint(-3, 2)
        for y in range(start, min(height, start + 18)):
            biases[y] = bias
    for _ in range(max(4, height // 140)):
        start = rng.randrange(max(1, height))
        band_height = rng.randint(1, 4)
        bias = -rng.randint(7, 18)
        for y in range(start, min(height, start + band_height)):
            biases[y] += bias
    return biases


def _apply_cached_thermal_ink_spread(image, rng: random.Random) -> None:
    """Slightly fatten rendered ink before paper noise is added."""
    pixels = image.load()
    spread_coords: set[tuple[int, int]] = set()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a < 255 or min(r, g, b) >= 170:
                continue
            for dx, dy, probability in ((1, 0, 0.28), (-1, 0, 0.12), (0, 1, 0.08)):
                if rng.random() >= probability:
                    continue
                nx = x + dx
                ny = y + dy
                if not (0 <= nx < image.width and 0 <= ny < image.height):
                    continue
                nr, ng, nb, na = pixels[nx, ny]
                if na == 255 and _is_cached_paper_pixel(nr, ng, nb):
                    spread_coords.add((nx, ny))

    for x, y in spread_coords:
        gray = rng.randint(42, 78)
        pixels[x, y] = (*_cached_thermal_warm_rgb(gray), pixels[x, y][3])


def _apply_cached_thermal_ink_contrast(image) -> None:
    """Darken rendered glyph pixels so density comes from ink, not just paper grain."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a < 255 or _is_cached_paper_pixel(r, g, b):
                continue
            value = min(r, g, b)
            if value < 80 or value >= 210:
                continue
            gray = max(28, min(118, int(round(value * 0.58))))
            pixels[x, y] = (*_cached_thermal_warm_rgb(gray), a)


def _apply_cached_thermal_mottle(image, rng: random.Random) -> None:
    """Add broad scanner/thermal paper variation without touching ink."""
    pixels = image.load()
    patch_count = max(5, image.height // _CACHED_THERMAL_MOTTLE_COUNT_FACTOR)
    for _ in range(patch_count):
        center_x = rng.randint(0, max(0, image.width - 1))
        center_y = rng.randint(0, max(0, image.height - 1))
        radius_x = rng.randint(max(32, image.width // 7), max(44, image.width // 2))
        radius_y = rng.randint(max(24, image.height // 20), max(42, image.height // 7))
        bias = rng.choice((-1, 1)) * rng.randint(10, 26)
        left = max(0, center_x - radius_x)
        right = min(image.width, center_x + radius_x)
        top = max(0, center_y - radius_y)
        bottom = min(image.height, center_y + radius_y)
        if right <= left or bottom <= top:
            continue
        inv_rx = 1.0 / max(1, radius_x)
        inv_ry = 1.0 / max(1, radius_y)
        for y in range(top, bottom):
            dy = (y - center_y) * inv_ry
            for x in range(left, right):
                dx = (x - center_x) * inv_rx
                distance = dx * dx + dy * dy
                if distance >= 1.0:
                    continue
                r, g, b, a = pixels[x, y]
                if a < 255 or not _is_cached_paper_pixel(r, g, b):
                    continue
                falloff = 1.0 - distance
                delta = int(round(bias * falloff))
                if not delta:
                    continue
                gray = min(250, max(210, ((r + g + b) // 3) + delta))
                pixels[x, y] = (*_cached_thermal_warm_rgb(gray), a)


def _apply_cached_thermal_scanline_banding(image, rng: random.Random) -> None:
    pixels = image.load()
    y = rng.randint(12, 28)
    while y < image.height:
        band_height = 1 if rng.random() < 0.94 else 2
        segment_count = 1 if rng.random() < 0.90 else 2
        for _ in range(segment_count):
            segment_width = int(image.width * rng.uniform(0.16, 0.42))
            left = rng.randint(-image.width // 8, image.width - 1)
            right = min(image.width, max(0, left) + max(12, segment_width))
            left = max(0, left)
            if right <= left:
                continue
            for yy in range(y, min(image.height, y + band_height)):
                for x in range(left, right):
                    r, g, b, a = pixels[x, yy]
                    if a < 255 or not _is_cached_paper_pixel(r, g, b):
                        continue
                    if rng.random() < 0.35:
                        value = rng.randint(160, 168)
                    else:
                        value = rng.randint(196, 226)
                    pixels[x, yy] = (*_cached_thermal_warm_rgb(value), a)
        y += rng.randint(
            _CACHED_THERMAL_SCANLINE_MIN_GAP,
            _CACHED_THERMAL_SCANLINE_MAX_GAP,
        )


def _apply_cached_thermal_density_floor(
    image,
    rng: random.Random,
    *,
    min_density: float,
) -> None:
    pixels = image.load()
    dark_count = 0
    paper_coords: list[tuple[int, int]] = []
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a < 255:
                continue
            if min(r, g, b) < 170:
                dark_count += 1
                continue
            if _is_cached_paper_pixel(r, g, b):
                paper_coords.append((x, y))

    target_dark = int(round(image.width * image.height * min_density))
    deficit = max(0, min(target_dark - dark_count, len(paper_coords)))
    if deficit <= 0:
        return
    for x, y in rng.sample(paper_coords, deficit):
        gray = rng.randint(150, 168)
        pixels[x, y] = (*_cached_thermal_warm_rgb(gray), pixels[x, y][3])


def _is_cached_paper_pixel(r: int, g: int, b: int) -> bool:
    return r >= 224 and g >= 224 and b >= 218


def _cached_thermal_warm_rgb(gray: int) -> tuple[int, int, int]:
    return (
        _clamp_cached_channel(gray + _CACHED_THERMAL_WARMTH_R),
        _clamp_cached_channel(gray + _CACHED_THERMAL_WARMTH_G),
        _clamp_cached_channel(gray + _CACHED_THERMAL_WARMTH_B),
    )


def _clamp_cached_channel(value: int) -> int:
    return max(0, min(255, int(value)))


def _overlay_cached_logo(
    image,
    receipt: dict,
    atlas,
    *,
    config: RenderConfig,
    coord_max: float,
) -> None:
    if atlas.logo is None:
        return
    logo_line = _cached_logo_line(receipt)
    if not logo_line:
        return
    bbox = _union_bbox([word["bbox"] for word in logo_line if word.get("bbox")])
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
    logo = atlas.logo
    scale = min(box_w / logo.width, box_h / logo.height)
    if scale <= 0:
        return
    size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
    scaled = logo.resize(size)
    x = int(left + (box_w - size[0]) / 2)
    y = int(top + (box_h - size[1]) / 2)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    pad = 3
    draw.rectangle(
        [left - pad, top - pad, right + pad, bottom + pad],
        fill=config.background + (255,),
    )
    image.alpha_composite(scaled, (max(0, x), max(0, y)))


def _overlay_cached_barcodes(
    image,
    receipt: dict,
    *,
    config: RenderConfig,
    coord_max: float,
) -> None:
    from PIL import ImageDraw

    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    draw = ImageDraw.Draw(image)
    for line in receipt.get("lines") or []:
        words = [word for word in line.get("words", []) if word.get("bbox")]
        digits = _cached_barcode_digits(words)
        if not digits:
            continue
        bbox = _union_bbox([word["bbox"] for word in words])
        if bbox is None:
            continue
        left, top, right, bottom = _to_pixel_box(
            bbox,
            coord_max=coord_max,
            margin=config.margin,
            inner_w=inner_w,
            inner_h=inner_h,
        )
        span = max(1.0, right - left)
        quiet = span * 0.08
        bx0 = left + quiet
        bx1 = right - quiet
        band_h = max(10.0, min((bottom - top) * 1.7, 18.0))
        band_bottom = top - 3.0
        band_top = max(float(config.margin), band_bottom - band_h)
        if bx1 - bx0 < 24.0 or band_bottom - band_top < 8.0:
            continue
        if _cached_band_has_ink(image, (bx0, band_top, bx1, band_bottom)):
            continue
        rng = random.Random(zlib.crc32(digits.encode("ascii")) & 0xFFFFFFFF)
        unit = max(1.0, (bx1 - bx0) / 95.0)
        x = bx0
        is_bar = True
        while x < bx1:
            width = rng.choice((1, 1, 2, 3)) * unit
            if is_bar:
                draw.rectangle(
                    [x, band_top, min(x + width, bx1), band_bottom],
                    fill=(34, 32, 30, 255),
                )
            x += width
            is_bar = not is_bar


def _cached_barcode_digits(words: list[dict]) -> str | None:
    tokens = [
        str(word.get("text") or "").strip()
        for word in sorted(words, key=lambda item: float(item["bbox"][0]))
    ]
    tokens = [token for token in tokens if token]
    if not tokens or not all(token.isdigit() for token in tokens):
        return None
    digits = "".join(tokens)
    return digits if _CACHED_BARCODE_RE.match(digits) else None


def _cached_band_has_ink(image, box: tuple[float, float, float, float]) -> bool:
    left = max(0, int(box[0]))
    top = max(0, int(box[1]))
    right = min(image.width, int(box[2] + 0.999))
    bottom = min(image.height, int(box[3] + 0.999))
    if right <= left or bottom <= top:
        return True
    gray = image.crop((left, top, right, bottom)).convert("L")
    dark = sum(1 for value in gray.getdata() if value < 170)
    return dark > max(3, int((right - left) * (bottom - top) * 0.012))


def _overlay_cached_qr_code(
    image,
    receipt: dict,
    *,
    config: RenderConfig,
    coord_max: float,
) -> None:
    """Draw a deterministic QR-like survey block for Sprouts public review."""
    from PIL import ImageDraw

    qr_box = _cached_qr_pixel_box(image, receipt, config=config, coord_max=coord_max)
    if qr_box is None:
        return
    left, top, right, bottom = qr_box
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [left - 8, top - 8, right + 8, bottom + 8],
        fill=config.background + (255,),
    )

    modules = _CACHED_QR_MODULES
    size = right - left
    cell = size / modules
    seed = _cached_qr_seed(receipt)
    rng = random.Random(seed)
    for row in range(modules):
        for col in range(modules):
            if (
                _qr_finder_cell(row, col, 0, 0)
                or _qr_finder_cell(row, col, modules - 7, 0)
                or _qr_finder_cell(row, col, 0, modules - 7)
            ):
                fill = (28, 27, 25, 255)
            elif _qr_finder_clearance(row, col, modules):
                continue
            else:
                value = rng.randrange(100)
                checker = ((row * 7 + col * 11 + seed) % 19) < 8
                if value > 47 and not checker:
                    continue
                fill = (36, 34, 32, 255)
            x0 = left + col * cell
            y0 = top + row * cell
            x1 = left + (col + 1) * cell
            y1 = top + (row + 1) * cell
            draw.rectangle([x0, y0, x1, y1], fill=fill)


def _cached_qr_pixel_box(
    image,
    receipt: dict,
    *,
    config: RenderConfig,
    coord_max: float,
) -> tuple[float, float, float, float] | None:
    feedback_line = _cached_line_with_text(receipt, "SPROUTSFEEDBACKCOM")
    if feedback_line is None:
        return None
    anchor_line = _cached_line_with_text(receipt, "5WINNERSMONTHLY") or feedback_line
    inner_w = config.width - 2 * config.margin
    inner_h = config.height - 2 * config.margin
    bbox = _union_bbox([word["bbox"] for word in anchor_line if word.get("bbox")])
    if bbox is None:
        return None
    _, _, _, feedback_bottom = _to_pixel_box(
        bbox,
        coord_max=coord_max,
        margin=config.margin,
        inner_w=inner_w,
        inner_h=inner_h,
    )
    size = min(
        _CACHED_QR_MAX_SIZE,
        max(_CACHED_QR_MIN_SIZE, image.width * _CACHED_QR_SIZE_FACTOR),
    )
    top = max(feedback_bottom + 12.0, image.height * _CACHED_QR_TOP_FACTOR)
    bottom_limit = image.height - config.margin - 72.0
    if top + size > bottom_limit:
        top = max(config.margin + 10.0, bottom_limit - size)
    left = (image.width - size) / 2.0
    return (left, top, left + size, top + size)


def _cached_line_with_text(receipt: dict, compact_text: str) -> list[dict] | None:
    target = _compact_text(compact_text)
    for line in receipt.get("lines") or []:
        words = [word for word in line.get("words", []) if word.get("bbox")]
        if _compact_line_text(words) == target:
            return words
    return None


def _cached_qr_seed(receipt: dict) -> int:
    texts = []
    for line in receipt.get("lines") or []:
        text = " ".join(str(word.get("text") or "") for word in line.get("words", []))
        if text:
            texts.append(text)
    payload = "\n".join(texts).encode("utf-8", "ignore")
    return zlib.crc32(payload) & 0xFFFFFFFF


def _qr_finder_cell(row: int, col: int, start_col: int, start_row: int) -> bool:
    local_row = row - start_row
    local_col = col - start_col
    if not (0 <= local_row < 7 and 0 <= local_col < 7):
        return False
    return (
        local_row in {0, 6}
        or local_col in {0, 6}
        or (2 <= local_row <= 4 and 2 <= local_col <= 4)
    )


def _qr_finder_clearance(row: int, col: int, modules: int) -> bool:
    return (
        (row < 8 and col < 8)
        or (row < 8 and col >= modules - 8)
        or (row >= modules - 8 and col < 8)
    )


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
                draw_qr_code=_cached_should_draw_qr(example),
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
                    draw_qr_code=_cached_should_draw_qr(example),
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
