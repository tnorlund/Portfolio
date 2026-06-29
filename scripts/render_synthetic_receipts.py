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


# Coordinate (of a 0..1000 space) that the right edge of a right-aligned price
# token is anchored to, so the price column does not collapse into the item name.
_PRICE_COLUMN_RIGHT = 905.0
# Trailing price/amount tokens: optional leading currency / sign, a decimal
# amount with two fractional digits, optional trailing sign (e.g. "1.99-").
_PRICE_TOKEN_RE = re.compile(r"^[-+]?\$?\d{1,3}(?:,\d{3})*\.\d{2}[-+]?$")


def _is_price_token(token: str) -> bool:
    return bool(_PRICE_TOKEN_RE.match(str(token or "").strip()))


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
                x += width + 8
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


def _composite_paper_texture(image, *, seed: int | None = None):
    """Composite subtle thermal-paper grain + edge vignette onto a render.

    Adds low-amplitude per-pixel luminance Gaussian noise (a few grey levels)
    plus a very faint radial vignette so the paper reads as fine-grained
    off-white instead of a dead-flat single-color fill. Returns a new image in
    the same mode as the input.
    """
    import numpy as np
    from PIL import Image

    source_mode = image.mode
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    height, width, _ = rgb.shape
    rng = np.random.default_rng(seed)

    # Fine luminance grain (same delta on R/G/B so it stays neutral/grey).
    grain = rng.normal(0.0, 3.0, size=(height, width, 1)).astype(np.float32)
    rgb += grain

    # Faint vignette: very slightly darker toward the edges/corners.
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float32)[:, None]
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float32)[None, :]
    radial = np.sqrt(xs * xs + ys * ys) / np.sqrt(2.0)
    vignette = (1.0 - 0.045 * (radial * radial))[:, :, None]
    rgb *= vignette

    rgb = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
    textured = Image.fromarray(rgb, mode="RGB")
    if source_mode == "RGB":
        return textured
    return textured.convert(source_mode)


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
        # Grid typography (fixed character grid, one body size per receipt, hard
        # non-anti-aliased glyphs on a shared baseline). The merchant profile
        # geometry is the realism control; min/max_font_px are only sanity clamps
        # (9px readability floor / 28px ceiling), not the per-token shrink that
        # used to make totals tiny and misaligned.
        min_font_px=9,
        max_font_px=28,
        grid_mode=True,
    )
    image = render_receipt(
        receipt,
        profile=profile,
        config=config,
        coord_max=1000.0,
    ).convert("RGBA")
    _overlay_cached_logo(image, receipt, atlas, config=config, coord_max=1000.0)
    _overlay_qr_and_barcode(image, receipt, config=config, coord_max=1000.0)
    # Deterministic per-output seed so re-rendering the same file is stable.
    texture_seed = zlib.crc32(os.path.basename(path).encode("utf-8"))
    image = _composite_paper_texture(image, seed=texture_seed)
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
    # Raise contrast so the wordmark reads as near-black thermal ink instead of
    # the faint light-gray crop straight off the aged paper photo.
    scaled = _darken_logo(logo).resize(size)
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


def _draw_qr(draw, x: int, y: int, size: int, seed: int) -> None:
    """Draw a synthetic-but-plausible QR block (finder patterns + modules)."""
    modules = 25
    cell = max(2, size // modules)
    span = cell * modules
    rng = __import__("random").Random(seed)
    dark = (20, 20, 20)
    light = (250, 249, 245)
    # Quiet-zone background.
    draw.rectangle([x - cell, y - cell, x + span + cell, y + span + cell], fill=light)

    def in_finder(r: int, c: int) -> bool:
        for fr, fc in ((0, 0), (0, modules - 7), (modules - 7, 0)):
            if fr <= r < fr + 7 and fc <= c < fc + 7:
                return True
        return False

    def draw_finder(fr: int, fc: int) -> None:
        ox, oy = x + fc * cell, y + fr * cell
        draw.rectangle([ox, oy, ox + 7 * cell, oy + 7 * cell], fill=dark)
        draw.rectangle(
            [ox + cell, oy + cell, ox + 6 * cell, oy + 6 * cell], fill=light
        )
        draw.rectangle(
            [ox + 2 * cell, oy + 2 * cell, ox + 5 * cell, oy + 5 * cell], fill=dark
        )

    for r in range(modules):
        for c in range(modules):
            if in_finder(r, c):
                continue
            if rng.random() < 0.5:
                cx, cy = x + c * cell, y + r * cell
                draw.rectangle([cx, cy, cx + cell, cy + cell], fill=dark)
    for fr, fc in ((0, 0), (0, modules - 7), (modules - 7, 0)):
        draw_finder(fr, fc)


def _draw_barcode(draw, x: int, y: int, w: int, h: int, seed: int) -> None:
    """Draw a synthetic 1D (Code128-style) barcode with a numeric caption."""
    rng = __import__("random").Random(seed)
    dark = (20, 20, 20)
    cx = x
    end = x + w
    # Quiet zone left/right is implicit (white paper).
    while cx < end:
        bar_w = rng.choice((1, 1, 2, 2, 3))
        if cx + bar_w > end:
            break
        if rng.random() < 0.5:
            draw.rectangle([cx, y, cx + bar_w, y + h], fill=dark)
        cx += bar_w
    digits = "".join(str(rng.randint(0, 9)) for _ in range(12))
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
    except OSError:
        from PIL import ImageFont

        font = ImageFont.load_default(size=13)
    tw = draw.textlength(digits, font=font)
    draw.text((x + (w - tw) / 2, y + h + 3), digits, font=font, fill=dark)


def _overlay_qr_and_barcode(
    image, receipt: dict, *, config: RenderConfig, coord_max: float
) -> None:
    """Stamp a QR block and a 1D barcode in the blank footer region.

    The renderer's footer narration ("Scan the QR code") promised anchors that
    were never drawn. These are synthetic placeholder graphics (they do not
    encode real data) keyed off the receipt so re-renders are stable; their job
    is to be present as believable visual anchors below the printed body.
    """
    from PIL import ImageDraw

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

    draw = ImageDraw.Draw(image)
    seed = zlib.crc32(("".join(str(b) for b in boxes[:8])).encode("utf-8"))
    cx = config.margin + inner_w / 2.0
    bar_w = int(inner_w * 0.6)
    bar_h = 46
    cap = 22
    qr_size = min(120, int(inner_w * 0.32))
    block_full = qr_size + 24 + bar_h + cap

    if avail_h >= block_full:
        y0 = int(gtop + (avail_h - block_full) / 2)
        _draw_qr(draw, int(cx - qr_size / 2), y0, qr_size, seed)
        _draw_barcode(
            draw, int(cx - bar_w / 2), y0 + qr_size + 24, bar_w, bar_h, seed ^ 0x5A5A
        )
    elif avail_h >= bar_h + cap + 6:
        y0 = int(gtop + (avail_h - (bar_h + cap)) / 2)
        _draw_barcode(draw, int(cx - bar_w / 2), y0, bar_w, bar_h, seed ^ 0x5A5A)


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
