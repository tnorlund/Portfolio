"""Git vendor package reads for the closed gold / export path.

``fonts/<slug>/font.json`` holds ``pitchRatioTarget`` and the studio preview
cap. ``fonts/<slug>/vendor.json`` holds recorded ``ocr_cap_height_ratio``,
``bitmap_thin``, and the per-vendor ``use_measured_separators`` /
``pin_pitch_ratio`` opt-ins.

Pin rule (export == a passing calibration):
  ``vendor.json`` is the export pin file. ``new_vendor.py calibrate`` writes
  ``ocr_cap_height_ratio`` there (and keeps the merchant profile in sync).
  ``cmd_export`` without ``--calibrate-from-corpus`` overlays that pin.

``preview.thin: "auto"`` is **not** a pin. A real number in ``font.json``
preview.thin, or ``vendor.json`` ``bitmap_thin``, is.
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

# Match ``RenderConfig.margin`` used by gold/export hybrid renders so
# ``font_height * inner_h`` recovers the recorded ``cap_px``.
CLOSED_PROFILE_MARGIN = 10.0
_FALLBACK_FONT_HEIGHT = 0.018
_FALLBACK_CHAR_WIDTH = 0.0125


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


def numeric_pin(value: Any) -> float | None:
    """A JSON number pin, or None for ``"auto"`` / missing / non-numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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

    ``preview.thin`` must be a JSON number to pin; the committed ``"auto"``
    string is not a pin. ``vendor.json`` ``bitmap_thin`` always wins when set.
    ``pitchRatioTarget`` is recorded for closed-profile char_width only;
    ``apply_gold_pins`` overlays it onto typography iff ``pin_pitch_ratio``.
    """
    pins: dict[str, Any] = {
        "pitch_ratio": None,
        "pin_pitch_ratio": False,
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
    pitch = numeric_pin(metrics.get("pitchRatioTarget"))
    if pitch is not None:
        pins["pitch_ratio"] = pitch
    cap_px = numeric_pin(preview.get("capPx"))
    if cap_px is not None:
        pins["cap_px"] = cap_px
    preview_thin = numeric_pin(preview.get("thin"))
    if preview_thin is not None:
        pins["bitmap_thin"] = preview_thin
    if vendor.get("ocr_cap_height_ratio") is not None:
        pins["ocr_cap_height_ratio"] = float(vendor["ocr_cap_height_ratio"])
    vendor_thin = numeric_pin(vendor.get("bitmap_thin"))
    if vendor_thin is not None:
        pins["bitmap_thin"] = vendor_thin
    pins["use_measured_separators"] = bool(
        vendor.get("use_measured_separators")
    )
    pins["pin_pitch_ratio"] = bool(vendor.get("pin_pitch_ratio"))
    vendor_pitch = numeric_pin(vendor.get("pitch_ratio"))
    if vendor_pitch is not None:
        pins["pitch_ratio"] = vendor_pitch
        pins["pin_pitch_ratio"] = True
    return pins


def closed_profile_geometry(
    pins: dict[str, Any] | None,
) -> tuple[float, float]:
    """Normalized ``font_height``, ``char_width`` for ``build_grid_spec``.

    Recorded ``cap_px`` becomes ``font_px`` once multiplied by the inner
    canvas (``canvas_height - 2 * margin``). Without a cap, the 0.018
    fallback is the old dead pin (≈45px on a 2497px Sprouts canvas).
    """
    pins = pins or {}
    cap_px = numeric_pin(pins.get("cap_px"))
    canvas = numeric_pin(pins.get("canvas_height"))
    if cap_px is not None and canvas is not None and canvas > 0:
        inner_h = max(1.0, canvas - 2.0 * CLOSED_PROFILE_MARGIN)
        font_height = cap_px / inner_h
    else:
        font_height = _FALLBACK_FONT_HEIGHT
    pitch = numeric_pin(pins.get("pitch_ratio"))
    char_width = (
        pitch * font_height if pitch is not None else _FALLBACK_CHAR_WIDTH
    )
    return font_height, char_width


def apply_gold_pins(typography: dict[str, Any], pins: dict[str, Any]) -> dict:
    """Overlay closed pins onto merchant typography. Never live-solves thin.

    ``pitch_ratio`` from ``font.json`` is a studio metric (closed-profile
    char_width). It is **not** written onto typography unless the vendor
    opted in via ``vendor.json`` ``pin_pitch_ratio`` or an explicit
    ``vendor.json`` ``pitch_ratio``. Overlaying it for every vendor turned
    The Stand's 0.647 clamp into 0.7426 and switched Sprouts' clamp on.
    """
    typ = dict(typography)
    if pins.get("pin_pitch_ratio") and pins.get("pitch_ratio") is not None:
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
    canvas_height: float | None = None,
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
    if canvas_height is not None:
        pins = dict(pins, canvas_height=float(canvas_height))
    typ = apply_gold_pins(typ, pins)
    return make_profile(merchant, pins), typ
