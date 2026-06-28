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
import sys

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
        return _line_receipt_from_cached_token_words(words)
    return {"words": words}


def _cached_line_receipt_dict(example: dict) -> dict:
    lines = []
    source_lines = _order_cached_sprouts_lines(
        _drop_duplicate_sprouts_header_lines(example)
    )
    for index, line in enumerate(source_lines):
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        words = text.split()
        y = float(line.get("y") or (940 - index * 16))
        labels = list(line.get("labels") or [])
        is_logo_line = any(_label_name(label) == "MERCHANT_NAME" for label in labels)
        # Cached line-only examples do not carry OCR word widths, so give the
        # renderer enough horizontal room to avoid shrinking every line to the
        # minimum font size.
        width_units = [max(18.0, len(word) * 12.0) for word in words]
        if is_logo_line and len(words) == 1:
            width_units = [max(width_units[0], 220.0)]
        total_width = sum(width_units) + max(0, len(words) - 1) * 8.0
        if total_width > 900:
            factor = 900 / total_width
            width_units = [width * factor for width in width_units]
            total_width = 900
        if is_logo_line:
            x = 500 - total_width / 2
        else:
            x = 70
        half_height = 24 if is_logo_line else 6
        rendered_words = []
        for word, width in zip(words, width_units):
            rendered_words.append(
                {
                    "text": word,
                    "bbox": [x, y - half_height, x + width, y + half_height],
                    "labels": labels,
                }
            )
            x += width + 8
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


def _line_receipt_from_cached_token_words(words: list[dict]) -> dict:
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
    return _cached_line_receipt_dict({"lines": lines})


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


def _sprouts_token_section(line: list[dict]) -> str:
    text = _compact_line_text(line)
    return _sprouts_text_section(text)


def _sprouts_text_section(text: str) -> str:
    if _is_sprouts_header_line(text):
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


def _cached_output_size(example: dict) -> tuple[int, int]:
    candidate_id = str(example.get("candidate_id") or "")
    if "address-line" in candidate_id or "hard_negative" in candidate_id:
        return (560, 1280)
    return (576, 1176)


def _render_cached_hybrid(
    receipt: dict,
    atlas,
    *,
    profile,
    width: int,
    height: int,
    path: str,
) -> str:
    config = RenderConfig(
        width=width,
        height=height,
        margin=10,
        color_by_label=False,
        draw_price_column=False,
        background=(250, 249, 245),
        min_font_px=5,
        max_font_px=10,
    )
    image = render_receipt(
        receipt,
        profile=profile,
        config=config,
        coord_max=1000.0,
    ).convert("RGBA")
    _overlay_cached_logo(image, receipt, atlas, config=config, coord_max=1000.0)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.convert("RGB").save(path, format="PNG")
    return path


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
