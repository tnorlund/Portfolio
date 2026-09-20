"""Git vendor package reads for the closed gold / export path.

``fonts/<slug>/font.json`` holds ``pitchRatioTarget`` and the studio preview
cap (``preview.capPx``, the cap height in px on the 760-wide export canvas).
``fonts/<slug>/vendor.json`` holds recorded ``ocr_cap_height_ratio``,
``bitmap_thin``, and the per-vendor ``use_measured_separators`` /
``pin_pitch_ratio`` opt-ins.

Pin rule (export == a passing calibration):
  ``vendor.json`` is the export pin file. ``new_vendor.py calibrate`` writes
  ``ocr_cap_height_ratio`` there (and keeps the merchant profile in sync).
  ``cmd_export`` without ``--calibrate-from-corpus`` overlays that pin.
  ``preview.thin: "auto"`` is **not** a pin. A number (or numeric string)
  in ``font.json`` preview.thin, or ``vendor.json`` ``bitmap_thin``, is.
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
    """Casefolded pipeline-card merchant name -> font slug."""
    if not os.path.isfile(MANIFEST):
        return {}
    merchants = load_json(MANIFEST).get("merchants") or {}
    out: dict[str, str] = {}
    for slug, spec in merchants.items():
        name = spec.get("merchant")
        if name:
            out[str(name).casefold()] = str(spec.get("font") or slug)
    return out


def _font_slug_from_typography(
    typography: dict[str, Any] | None,
) -> str | None:
    """The ``fonts/<slug>`` package a truth-bundle ``bitmap_font`` names.

    Every glyph-studio face is published as ``<slug>.glyphs.npz`` /
    ``<slug>-heavy.glyphs.npz`` (Costco's ``bitMatrix-C2`` is the exception
    and carries a vendor.json instead), so the regular face's stem IS the
    package slug when ``fonts/<stem>/font.json`` exists.
    """
    faces = (typography or {}).get("bitmap_font")
    if not isinstance(faces, dict):
        return None
    for face in ("regular", "heavy"):
        path = faces.get(face)
        if not path:
            continue
        stem = os.path.basename(str(path))
        for suffix in (".glyphs.npz", ".npz"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if stem.endswith("-heavy"):
            stem = stem[: -len("-heavy")]
        if stem and os.path.isfile(font_path(stem)):
            return stem
    return None


def slug_for_merchant(
    merchant: str | None, typography: dict[str, Any] | None = None
) -> str | None:
    """The ``fonts/<slug>`` package for ``merchant``, else ``None``.

    Resolution order: a ``vendor.json`` naming the merchant (or an alias),
    the pipeline card for the merchant (casefolded), then the font package
    the merchant's truth-bundle ``bitmap_font`` faces belong to. The last
    step covers merchants with a committed ``font.json`` but no vendor.json
    or card (The Stand, The Home Depot, Gelson's, ...), so their recorded
    cap and pitch reach the closed profile instead of the generic fallback.
    """
    rec = vendor_record_for_merchant(merchant)
    if rec.get("slug"):
        return str(rec["slug"])
    name = (merchant or "").strip()
    if name:
        hit = _pipeline_merchant_to_slug().get(name.casefold())
        if hit:
            return hit
    return _font_slug_from_typography(typography)


# RenderConfig.ocr_cap_height_ratio default; the closed profile inverts the
# same ratio _ocr_grid_metrics applies (cap_px = word height * ratio).
DEFAULT_CAP_HEIGHT_RATIO = 0.72
# _render_cached_hybrid renders every gold/export canvas with this margin.
GOLD_CANVAS_MARGIN = 10
# font.json preview.capPx is the cap height on the 760-wide export canvas
# (pipeline_assets.RECEIPT_WIDTH); other widths scale it proportionally.
GOLD_REFERENCE_WIDTH = 760


def _numeric_pin(value: Any) -> float | None:
    """A pin as float; ``None`` for absent, ``"auto"``, or unparsable values."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if not text or text == "auto":
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


numeric_pin = _numeric_pin  # public name (tests, tooling)


def closed_font_height(
    cap_px: float | None,
    cap_height_ratio: float | None,
    canvas_height: int | None,
    *,
    canvas_width: int | None = None,
    reference_width: int = GOLD_REFERENCE_WIDTH,
    margin: int = GOLD_CANVAS_MARGIN,
) -> float | None:
    """Normalized ``font_height`` implied by a recorded cap on a canvas.

    ``cached_font_profile`` records ``font_height`` as the median OCR word-box
    height over the receipt height; ``build_grid_spec`` turns that into
    ``font_px = font_height * inner_h`` and ``_ocr_grid_metrics`` measures
    ``cap_px = word_height * ocr_cap_height_ratio``. Inverting both gives the
    closed value: ``(cap_px / ratio) / (canvas_height - 2 * margin)``.

    The recorded cap is defined on the ``reference_width`` (760 px) export
    canvas; when ``canvas_width`` is given the cap scales by
    ``canvas_width / reference_width`` so a render at another resolution
    keeps the same glyph-to-paper proportion. ``None`` when either the cap
    or the canvas height is unknown (callers fall back).
    """
    if cap_px is None or canvas_height is None:
        return None
    inner_h = float(canvas_height) - 2.0 * float(margin)
    cap = float(cap_px)
    if canvas_width is not None and reference_width:
        cap *= float(canvas_width) / float(reference_width)
    if cap <= 0 or inner_h <= 0:
        return None
    ratio = float(cap_height_ratio or DEFAULT_CAP_HEIGHT_RATIO)
    ratio = max(0.65, min(0.95, ratio))  # receipt_renderer clamps the same
    return (cap / ratio) / inner_h


def gold_render_pins(slug: str | None) -> dict[str, Any]:
    """Closed gold knobs from ``font.json`` + ``vendor.json``.

    Missing files yield an empty pin set; callers then keep already-recorded
    typography values and still must not live-solve ``bitmap_thin``.

    ``bitmap_thin`` reads ``font.json`` ``preview.thin`` when it is numeric
    (a number or a numeric string); the literal ``"auto"`` (what every
    committed font.json carries today) is ignored, so in practice only
    ``vendor.json`` pins thin. A ``vendor.json`` value always wins.
    """
    pins: dict[str, Any] = {
        "pitch_ratio": None,
        "ocr_cap_height_ratio": None,
        "bitmap_thin": None,
        "cap_px": None,
        "use_measured_separators": False,
        "pin_pitch_ratio": False,
        "slug": slug,
    }
    if not slug:
        return pins
    font = load_font_doc(slug) or {}
    vendor = load_vendor_doc(slug) or {}
    metrics = font.get("metrics") or {}
    preview = font.get("preview") or {}
    pitch = _numeric_pin(metrics.get("pitchRatioTarget"))
    if pitch is not None:
        pins["pitch_ratio"] = pitch
    cap_px = _numeric_pin(preview.get("capPx"))
    if cap_px is not None:
        pins["cap_px"] = cap_px
    preview_thin = _numeric_pin(preview.get("thin"))
    if preview_thin is not None:
        pins["bitmap_thin"] = preview_thin
    cap_ratio = _numeric_pin(vendor.get("ocr_cap_height_ratio"))
    if cap_ratio is not None:
        pins["ocr_cap_height_ratio"] = cap_ratio
    vendor_thin = _numeric_pin(vendor.get("bitmap_thin"))
    if vendor_thin is not None:
        pins["bitmap_thin"] = vendor_thin
    pins["use_measured_separators"] = bool(
        vendor.get("use_measured_separators")
    )
    pins["pin_pitch_ratio"] = bool(vendor.get("pin_pitch_ratio"))
    # an explicit vendor.json pitch_ratio is itself an opt-in pin
    vendor_pitch = _numeric_pin(vendor.get("pitch_ratio"))
    if vendor_pitch is not None:
        pins["pitch_ratio"] = vendor_pitch
        pins["pin_pitch_ratio"] = True
    return pins


def apply_gold_pins(typography: dict[str, Any], pins: dict[str, Any]) -> dict:
    """Overlay closed pins onto merchant typography. Never live-solves thin.

    ``font.json`` ``pitchRatioTarget`` is a studio authoring target, not a
    per-merchant truth value: it only fills a typography block that has no
    ``pitch_ratio`` of its own (the advance clamp stays off otherwise), or
    replaces the recorded one when ``vendor.json`` opts in with
    ``"pin_pitch_ratio": true`` (the vendors whose gold was recorded against
    the font.json pitch). Every other vendor keeps its truth-bundle pitch.
    """
    typ = dict(typography)
    pitch_pin = pins.get("pitch_ratio")
    if pitch_pin is not None and (
        typ.get("pitch_ratio") is None or pins.get("pin_pitch_ratio")
    ):
        typ["pitch_ratio"] = float(pitch_pin)
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
    canvas_height: int | None = None,
    canvas_width: int | None = None,
):
    """Profile + typography for gold/export: git pins, no live 12-receipt thin.

    ``rsr`` and ``make_profile`` are injected so this module stays importable
    without the renderer stack. ``--calibrate-from-corpus`` restores
    ``rsr.corpus_font_inputs`` (``cached_font_profile(n=12)`` +
    ``resolve_bitmap_thin``, shared with glyph_review). ``canvas_height`` /
    ``canvas_width`` are the render canvas; ``make_profile`` receives them so
    the closed profile's ``font_height`` matches the 12-receipt profile
    dimensionally and scales with the canvas (see
    :func:`closed_font_height`).
    """
    typ = dict(typ)
    if calibrate_from_corpus:
        # the same 12-receipt/thin block glyph_review renders with
        return rsr.corpus_font_inputs(
            table,
            merchant,
            region=region,
            typography=typ,
            atlas=atlas,
            section_scale=section_scale,
        )
    pins = gold_render_pins(slug_for_merchant(merchant, typ))
    typ = apply_gold_pins(typ, pins)
    prof = make_profile(
        merchant, pins, canvas_height=canvas_height, canvas_width=canvas_width
    )
    return prof, typ
