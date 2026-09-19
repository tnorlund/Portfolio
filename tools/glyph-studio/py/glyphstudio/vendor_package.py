"""Git vendor package reads for the closed gold / export path.

``fonts/<slug>/font.json`` holds ``pitchRatioTarget`` and the studio preview
cap. ``fonts/<slug>/vendor.json`` holds recorded ``ocr_cap_height_ratio``,
``bitmap_thin``, and the per-vendor ``use_measured_separators`` opt-in.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.abspath(os.path.join(_HERE, "..", ".."))
ROOT = os.path.abspath(os.path.join(STUDIO, "..", ".."))
FONTS_DIR = os.path.join(STUDIO, "fonts")
MANIFEST = os.path.join(STUDIO, "fixtures", "pipeline_merchants.json")


def vendor_path(slug: str) -> str:
    return os.path.join(FONTS_DIR, slug, "vendor.json")


def font_path(slug: str) -> str:
    return os.path.join(FONTS_DIR, slug, "font.json")


def load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_vendor_doc(slug: str) -> dict[str, Any] | None:
    path = vendor_path(slug)
    if not os.path.isfile(path):
        return None
    doc = load_json(path)
    doc.setdefault("slug", slug)
    return doc


def load_font_doc(slug: str) -> dict[str, Any] | None:
    path = font_path(slug)
    if not os.path.isfile(path):
        return None
    return load_json(path)


@lru_cache(maxsize=1)
def _vendor_records() -> tuple[dict[str, Any], ...]:
    if not os.path.isdir(FONTS_DIR):
        return ()
    records = []
    for slug in sorted(os.listdir(FONTS_DIR)):
        doc = load_vendor_doc(slug)
        if doc is not None:
            records.append(doc)
    return tuple(records)


def vendor_record_for_merchant(merchant: str | None) -> dict[str, Any]:
    """The vendor.json whose merchant / aliases match ``merchant``, else {}."""
    name = merchant or ""
    if not name:
        return {}
    want = name.casefold()
    for rec in _vendor_records():
        names = [rec.get("merchant"), *(rec.get("aliases") or ())]
        if any(str(n).casefold() == want for n in names if n):
            return rec
    return {}


def vendor_uses_measured_separators(merchant: str | None) -> bool:
    return bool(
        vendor_record_for_merchant(merchant).get("use_measured_separators")
    )


@lru_cache(maxsize=1)
def _pipeline_merchant_to_slug() -> dict[str, str]:
    if not os.path.isfile(MANIFEST):
        return {}
    merchants = load_json(MANIFEST).get("merchants") or {}
    out: dict[str, str] = {}
    for slug, spec in merchants.items():
        name = spec.get("merchant")
        if name:
            out[str(name)] = str(spec.get("font") or slug)
    return out


def slug_for_merchant(merchant: str | None) -> str | None:
    rec = vendor_record_for_merchant(merchant)
    if rec.get("slug"):
        return str(rec["slug"])
    name = merchant or ""
    if not name:
        return None
    return _pipeline_merchant_to_slug().get(name)


def gold_render_pins(slug: str | None) -> dict[str, Any]:
    """Closed gold knobs from ``font.json`` + ``vendor.json``.

    Missing files yield an empty pin set; callers then keep already-recorded
    typography values and still must not live-solve ``bitmap_thin``.
    """
    pins: dict[str, Any] = {
        "pitch_ratio": None,
        "ocr_cap_height_ratio": None,
        "bitmap_thin": None,
        "cap_px": None,
        "use_measured_separators": False,
        "slug": slug,
    }
    if not slug:
        return pins
    font = load_font_doc(slug) or {}
    vendor = load_vendor_doc(slug) or {}
    metrics = font.get("metrics") or {}
    preview = font.get("preview") or {}
    pitch = metrics.get("pitchRatioTarget")
    if pitch is not None:
        pins["pitch_ratio"] = float(pitch)
    cap_px = preview.get("capPx")
    if cap_px is not None:
        pins["cap_px"] = float(cap_px)
    preview_thin = preview.get("thin")
    if isinstance(preview_thin, (int, float)):
        pins["bitmap_thin"] = float(preview_thin)
    if vendor.get("ocr_cap_height_ratio") is not None:
        pins["ocr_cap_height_ratio"] = float(vendor["ocr_cap_height_ratio"])
    if vendor.get("bitmap_thin") is not None:
        pins["bitmap_thin"] = float(vendor["bitmap_thin"])
    pins["use_measured_separators"] = bool(
        vendor.get("use_measured_separators")
    )
    return pins


def apply_gold_pins(typography: dict[str, Any], pins: dict[str, Any]) -> dict:
    """Overlay closed pins onto merchant typography. Never live-solves thin."""
    typ = dict(typography)
    if pins.get("pitch_ratio") is not None:
        typ["pitch_ratio"] = float(pins["pitch_ratio"])
    if pins.get("ocr_cap_height_ratio") is not None:
        typ["ocr_cap_height_ratio"] = float(pins["ocr_cap_height_ratio"])
    if pins.get("bitmap_thin") is not None:
        typ["bitmap_thin"] = float(pins["bitmap_thin"])
    elif "bitmap_thin" not in typ:
        typ["bitmap_thin"] = 0.0
    return typ


def resolve_gold_inputs(
    merchant,
    typ,
    *,
    table,
    region,
    make_profile,
    rsr: Any = None,
    calibrate_from_corpus: bool = False,
    atlas=None,
    section_scale=None,
):
    """Profile + typography for gold/export: git pins, no live 12-receipt thin.

    ``rsr`` and ``make_profile`` are injected so this module stays importable
    without the renderer stack. ``--calibrate-from-corpus`` restores
    ``cached_font_profile(n=12)`` + ``resolve_bitmap_thin``.
    """
    typ = dict(typ)
    if calibrate_from_corpus:
        prof = rsr.cached_font_profile(
            table, merchant, region=region, max_receipts=12
        )
        if "bitmap_font" in typ and "bitmap_thin" not in typ:
            typ["bitmap_thin"] = rsr.resolve_bitmap_thin(
                table,
                merchant,
                region=region,
                atlas=atlas,
                profile=prof,
                section_scale=section_scale,
                typography=typ,
            )
        return prof, typ
    pins = gold_render_pins(slug_for_merchant(merchant))
    typ = apply_gold_pins(typ, pins)
    return make_profile(merchant, pins), typ
