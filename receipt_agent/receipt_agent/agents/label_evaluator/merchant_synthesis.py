"""Generic merchant receipt parameterization and synthesis.

This module is the merchant-agnostic bridge between the LLM's pattern
understanding and LayoutLM train-only examples. It intentionally uses the
same structured receipt data that pattern discovery sees: labels, token
geometry, merchant-local examples, and line-item arithmetic anchors.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
import re
import statistics
from typing import Any

from receipt_dynamo.constants import CORE_LABELS

from .merchant_tax_config import (
    merchant_supports_taxable_edits,
    merchant_tax_profile,
    merchant_taxable_edit_rate,
)
from .store_profile import (
    StoreProfile,
    alternate_profiles,
    extract_store_profiles,
    reflow_line_boxes,
)
from .synthesis_reconcile import reconcile_candidate
from .synthesis_receipt_model import reconcile_and_validate


CORE_LABEL_SET = set(CORE_LABELS.keys())
UNKNOWN_CATEGORY = "UNCATEGORIZED"
GENERIC_CATEGORY_HEADINGS = {
    "APPAREL",
    "BAKERY",
    "BEER",
    "BEVERAGES",
    "BULK",
    "CLOTHING",
    "CRV",
    "DAIRY",
    "DELI",
    "DRINKS",
    "ELECTRONICS",
    "FLORAL",
    "FOOD",
    "FROZEN",
    "GROCERY",
    "HOUSEWARES",
    "MEAT",
    "PHARMACY",
    "PRODUCE",
    "SEAFOOD",
    "VITAMINS",
    "WINE",
}
AMOUNT_LABELS = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "DISCOUNT",
    "COUPON",
    "TIP",
    "CHANGE",
    "CASH_BACK",
    "REFUND",
}
SYNTHETIC_MIN_STRUCTURE_SIMILARITY = 0.60
SYNTHETIC_STRUCTURE_COMPONENT_THRESHOLDS = {
    "price_column": 0.75,
    "line_step": 0.45,
    "category_sequence": 0.40,
    "category_set": 0.40,
    "token_count": 0.35,
}


@dataclass
class GeometrySummary:
    """Compact percentile summary for one coordinate family."""

    n: int
    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
        }


@dataclass
class MerchantLineItem:
    """Parsed line item paired with a price row."""

    line_index: int
    line_indices: list[int]
    amount: Decimal
    product_text: str
    center_y: float
    taxable: bool
    category: str = UNKNOWN_CATEGORY
    # The item's full vertical BAND — every contiguous receipt line it occupies
    # (code / name / flag / quantity / price), not just the labeled name+total
    # lines, plus the band's pixel extent (y-high-is-top, so top_y > bottom_y).
    # Populated by _segment_item_bands; drives gap-aware insertion, whole-band
    # removal, and whole-band cloning.
    band_line_indices: list[int] = field(default_factory=list)
    band_top_y: float = 0.0
    band_bottom_y: float = 0.0


@dataclass
class MerchantAnalysis:
    """Parsed structure for one real or synthetic receipt."""

    receipt: dict[str, Any]
    line_items: list[MerchantLineItem]
    subtotal: Decimal | None
    tax_total: Decimal | None
    grand_total: Decimal | None
    grand_total_line_indices: list[int]
    category_sequence: list[str] = field(default_factory=list)


@dataclass
class MerchantCatalogEntry:
    """Observed item text, price, and category evidence."""

    product_text: str
    amount: Decimal
    category: str
    taxable: bool
    count: int
    source_receipt_keys: list[str]
    # Verbatim real row group(s) this item was observed in, keyed by source
    # receipt key. Each captured group preserves the merchant's full row grammar
    # (product code, tax flag, exact price token, two-line layout) so the
    # add-item synthesizer can transplant a real row instead of rebuilding a
    # lossy "name + price" approximation. See _capture_row_group.
    source_rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def product_tokens(self) -> list[str]:
        return self.product_text.split()

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_text": self.product_text,
            "product_tokens": self.product_tokens,
            "line_total": _format_money(self.amount),
            "category": self.category,
            "taxable": self.taxable,
            "observed_count": self.count,
            "source_receipt_keys": self.source_receipt_keys[:5],
        }


@dataclass
class OnlineCatalogEntry:
    """A real product sourced from a merchant's ONLINE catalog: name + price
    (+ optional UPC).

    Unlike ``MerchantCatalogEntry`` (mined from our own receipts and bounded by
    what we have already seen), this supplies fresh CONTENT that template-fill
    renders into the merchant's real row FORMAT with labels WE assign — so the
    item-region supervision is clean by construction, instead of inheriting the
    base receipt's label noise the way a transplanted real row does.
    """

    name: str
    price: Decimal
    upc: str = ""
    taxable: bool = True
    source: str = "online_catalog"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_total": _format_money(self.price),
            "upc": self.upc,
            "taxable": self.taxable,
            "source": self.source,
        }


# Verified online catalog entries — name + price confirmed against the
# merchant's public storefront, UPCs taken from real product listings. Keyed by
# normalized (lowercased) merchant name. Callers may also inject their own list
# via ``generate_merchant_synthesis_candidates(online_catalog=...)``.
_MERCHANT_ONLINE_CATALOG: dict[str, list[OnlineCatalogEntry]] = {
    "the home depot": [
        OnlineCatalogEntry(
            "FEIT 100W ST19 AMBER LED 4PK", Decimal("28.97"), "017801185775"
        ),
        OnlineCatalogEntry(
            "GRIP-RITE 1-1/4 DRYWALL SCREW 1LB", Decimal("5.97"), "764666104983"
        ),
        OnlineCatalogEntry(
            "GORILLA WOOD GLUE 8OZ", Decimal("4.98"), "052427620002"
        ),
        OnlineCatalogEntry(
            "BEHR PREM PLUS INT FLAT 1GAL", Decimal("26.98"), "082901105001"
        ),
        OnlineCatalogEntry(
            "DAP ALEX PLUS CAULK 10.1OZ WHT", Decimal("3.98"), "070798181030"
        ),
    ],
}


# Directory of per-merchant online-catalog JSON files. Each file is one merchant
# ({"merchant_name": str, "entries": [{"name", "price", "upc"?, "taxable"?}]}),
# so parallel agents can each contribute a merchant's catalog as a SEPARATE file
# with no merge conflicts. Files merge with (and extend) the in-code catalog.
_ONLINE_CATALOG_DIR = Path(__file__).resolve().parent / "online_catalogs"


@functools.lru_cache(maxsize=1)
def _file_online_catalogs() -> dict[str, tuple[OnlineCatalogEntry, ...]]:
    catalogs: dict[str, list[OnlineCatalogEntry]] = {}
    if not _ONLINE_CATALOG_DIR.is_dir():
        return {}
    for path in sorted(_ONLINE_CATALOG_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        merchant = str(data.get("merchant_name") or "").strip().lower()
        entries: list[OnlineCatalogEntry] = []
        for raw in data.get("entries") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            price = _parse_money(raw.get("price") or raw.get("line_total"))
            if not name or price is None or price <= Decimal("0.00"):
                continue
            entries.append(
                OnlineCatalogEntry(
                    name=name,
                    price=price,
                    upc=str(raw.get("upc") or "").strip(),
                    taxable=bool(raw.get("taxable", True)),
                    source=f"online_catalog_file:{path.name}",
                )
            )
        if merchant and entries:
            catalogs.setdefault(merchant, []).extend(entries)
    return {merchant: tuple(items) for merchant, items in catalogs.items()}


def _merchant_online_catalog(
    merchant_name: str,
    override: list[OnlineCatalogEntry] | None = None,
) -> list[OnlineCatalogEntry]:
    """Online catalog entries for a merchant (explicit override wins, else the
    in-code catalog plus any per-merchant JSON file in ``online_catalogs/``)."""
    if override:
        return list(override)
    key = merchant_name.strip().lower()
    entries = list(_MERCHANT_ONLINE_CATALOG.get(key, []))
    entries.extend(_file_online_catalogs().get(key, ()))
    return entries


# ---------------------------------------------------------------------------
# Per-merchant item-line GRAMMAR (markdown marker + sale sub-line) wiring.
#
# The merchant-research export carries a derived item-line grammar
# (``extract_item_line_template``): whether discounted rows print a "NOW" sale
# marker left of the price and whether a "SALE 1@ $x, WAS: $y each" sub-line is
# printed beneath the item. We lazily load that grammar (cached per slug) and
# render it into the synthesized item rows so a composed/added Amazon row looks
# like a real Amazon row, while merchants without the grammar (e.g. Costco) are
# unchanged.
# ---------------------------------------------------------------------------

# Markdown / "sale now" marker tokens (OCR is noisy: NOW / now / nOW ...).
# Mirrors merchant_research.item_line_grammar._MARKER_RE so the label verifier
# recognizes a rendered marker without importing the research module.
_MARKDOWN_MARKER_RE = re.compile(r"^n[o0]w$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Canonical item-line GRAMMAR protection across the reconcile text-clean pass.
#
# The rendered markdown marker ("now") and sale sub-line keyword ("SALE") are
# clean, canonical synthetic tokens, but they are supervision-neutral ('O') copy
# and ``synthesis_text_clean.clean_token_text`` vocab-repairs O-labeled words.
# Because "now"/"sale" are edit-distance 1 from common receipt vocabulary
# ("how"/"sales"), that pass mis-"repairs" them (now->how, SALE->SALES), baking
# OCR-shaped noise back into the ground truth. We restore the two KNOWN phrases
# AFTER reconcile, gated on the SAME structural signatures the grammar detects
# them on (marker adjacent to a price / tax flag; sale keyword leading a
# "... WAS ..." sub-line), so unrelated words -- the survey "how", a "Sales tax"
# line, a card "SALE" -- are left untouched.
_GRAMMAR_PRICE_TOKEN_RE = re.compile(r"^\$?\d+\.\d{2}")
# now/how OCR variants of the single "now" sale marker.
_MARKER_NOISE_RE = re.compile(r"^[hn][o0]w$", re.IGNORECASE)
# SALE/SAVE/SALES/SAVES OCR variants of the sub-line's leading sale keyword.
_SALE_NOISE_RE = re.compile(r"^sa[lv]es?$", re.IGNORECASE)
# Single/double-char tax-flag codes a transplanted marker can sit immediately
# right of ("<flag> now ..."); mirrors item_line_grammar.FLAG_ALPHABET.
_GRAMMAR_FLAG_TOKENS = frozenset(
    {"A", "B", "E", "F", "N", "P", "S", "T", "X", "NF", "NT"}
)


def _restore_canonical_grammar_tokens(
    tokens: list[str], ner_tags: list[str]
) -> list[str]:
    """Restore the canonical markdown marker / sale sub-line keyword after the
    reconcile text-clean pass has mis-"repaired" them (now->how, SALE->SALES).

    Pure over ``tokens`` (tags/bboxes untouched); returns the corrected token
    list. Only O-labeled tokens matching a marker/sale signature are restored.
    """
    out = list(tokens)
    n = len(out)
    for i, tok in enumerate(out):
        if (ner_tags[i] if i < len(ner_tags) else "O") != "O":
            continue
        text = str(tok)
        if _MARKER_NOISE_RE.match(text):
            nxt = str(out[i + 1]) if i + 1 < n else ""
            prev = str(out[i - 1]).upper() if i > 0 else ""
            # Marker sits immediately LEFT of the price, or (transplanted rows)
            # immediately RIGHT of a tax flag: "now $4.99" / "F now".
            if _GRAMMAR_PRICE_TOKEN_RE.match(nxt) or prev in _GRAMMAR_FLAG_TOKENS:
                out[i] = "now"
        elif _SALE_NOISE_RE.match(text):
            # Sale keyword leading a "... WAS ..." sub-line (the same SALE...WAS
            # signature the grammar detects the sub-line on).
            if any(
                str(out[j]).upper().startswith("WAS")
                for j in range(i + 1, min(n, i + 5))
            ):
                out[i] = "SALE"
    return out


def _merchant_research_slug(merchant_name: str | None) -> str:
    """Slug a merchant name to its merchant-research export key (``amazon_fresh``,
    ``costco_wholesale``).

    Mirrors ``merchant_tax_config._slug``: apostrophes are dropped before the
    non-alphanumeric split so "Smith's" collapses to ``smiths`` (not
    ``smith_s``), and the result is underscore-joined to match the export
    filenames.
    """
    cleaned = re.sub(r"['‘’ʼ`]", "", str(merchant_name or "").lower())
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")


@functools.lru_cache(maxsize=None)
def _item_line_template_for_merchant(slug: str):
    """The merchant's derived item-line grammar (``ItemLineTemplate``) or ``None``.

    Lazily imports the merchant-research export loader + extractor so this module
    has no hard dependency on the research package; returns ``None`` — never
    raises — when the slug is empty, the export is missing, or it fails to parse,
    so a merchant without a grammar simply renders the existing plain rows.
    """
    if not slug:
        return None
    try:
        from .merchant_research.item_line_grammar import (
            extract_item_line_template,
            load_merchant_export,
        )

        export = load_merchant_export(slug)
        return extract_item_line_template(export)
    except Exception:  # missing export / parse failure -> no grammar
        return None


def _template_word_gap(line_height: float) -> int:
    """Smallest verifier-safe inter-word gap for a row of ``line_height`` px,
    rounded to whole pixels for integer bboxes.

    Mirrors ``synthesis_reconcile._min_word_gap`` (the objective verifier flags an
    adjacent word pair whose gap is ``< 0.30 * line_height``): target that bound
    plus a small rounding cushion so a rendered row opens a single tight
    word-space instead of the old wide ``char_w`` "justified typewriter" sprawl.
    """
    h = max(float(line_height), 6.0)
    return max(int(round(_VERIFIER_WORD_GAP_FRAC * h + 1.5)), 3)


# Mirror of synthesis_reconcile._VERIFIER_WORD_GAP_FRAC (kept local so this module
# has no import dependency on it).
_VERIFIER_WORD_GAP_FRAC = 0.30

# Mirror of glyph_renderer._AMOUNT_LABELS / _AMOUNT_RE: a right-aligned amount
# token (price / total / tax) is snapped to its ending grid cell at render time,
# so a sub-cell gap after it collapses the next token onto the price's edge
# ("$4.39F", "$7.49each"). We only need to widen the boundary AFTER such a token.
_AMOUNT_LABELS = frozenset(
    {
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
)
_AMOUNT_TOKEN_RE = re.compile(r"^\$?\d{1,4}(?:,\d{3})*\.\d{2}\$?[A-Z]?-?$")


def _is_amount_token(word: dict[str, Any]) -> bool:
    labels = {
        str(label).split("-", 1)[-1] for label in (word.get("labels") or [])
    }
    if labels & _AMOUNT_LABELS:
        return True
    return bool(_AMOUNT_TOKEN_RE.match(str(word.get("text") or "").strip()))


def _render_cell_width(receipt: dict[str, Any]) -> int:
    """The receipt's median per-character width = the fixed-pitch renderer's
    character cell. Mirrors ``glyph_renderer._pitch_norm`` (median word_width /
    char_count) so a de-glue nudge uses the same cell the renderer snaps to."""
    advances: list[float] = []
    for line in receipt.get("lines") or []:
        for word in line.get("words") or []:
            box = word.get("bbox")
            text = str(word.get("text") or "").strip()
            if not box or len(box) < 4 or len(text) < 2:
                continue
            width = abs(float(box[2]) - float(box[0]))
            if width > 0:
                advances.append(width / len(text))
    if not advances:
        return 12
    return max(6, int(round(statistics.median(advances))))


def _ensure_amount_word_gaps(
    lines: list[dict[str, Any]], *, cell_w: int
) -> None:
    """De-GLUE (only) the token that immediately FOLLOWS an amount token.

    The grid renderer right-aligns amounts to their ending cell and then only
    prevents the next word from overlapping. A real/cloned row whose price butts
    against a trailing tax flag ("$4.39F") or a sale value against "each"
    ("$7.49each") would render glued. We open a gap ONLY when the following token
    sits below the verifier-safe minimum -- a TIGHT single word-space, not a full
    character cell. Bloating the SOURCE gap to a whole cell was a wide-spacing
    tell; the renderer's per-row cursor (``draw_grid_line``) advances past a
    right-anchored price so the flag/unit still renders visibly separated from a
    tight source gap. Already-spaced name columns (and every label / token /
    reading order) are left untouched."""
    cell = max(1, int(round(cell_w)))
    for line in lines:
        words = sorted(
            (w for w in line.get("words") or [] if w.get("bbox")),
            key=lambda w: w["bbox"][0],
        )
        for prev, word in zip(words, words[1:]):
            if not _is_amount_token(prev):
                continue
            prev_box = prev["bbox"]
            height = max(1, int(prev_box[3]) - int(prev_box[1]))
            # Tight verifier-safe gap (mirrors _template_word_gap), capped at one
            # cell so a tall row never reintroduces a wide gap.
            gap = min(_template_word_gap(height), cell)
            box = word["bbox"]
            min_x0 = int(prev_box[2]) + gap
            if int(box[0]) < min_x0:
                width = max(1, int(box[2]) - int(box[0]))
                box[0] = min_x0
                box[2] = min(1000, min_x0 + width)
        if words:
            line["y"] = _line_y(line)


def build_merchant_synthesis_profile(
    merchant_name: str,
    receipts_data: list[dict[str, Any]],
    *,
    online_catalog: list[OnlineCatalogEntry] | None = None,
) -> dict[str, Any] | None:
    """Build a merchant-local synthesis profile from current receipt data."""
    receipts = [
        receipt
        for receipt in (
            _normalize_receipt(receipt) for receipt in receipts_data
        )
        if receipt["words"]
    ]
    if not receipts:
        return None

    all_analyses = [_analyze_receipt(receipt) for receipt in receipts]
    analyses = [analysis for analysis in all_analyses if analysis.line_items]
    catalog = _build_item_catalog(analyses)
    real_structure_baseline = build_real_structure_baseline(analyses)
    label_slots = {
        label: slot
        for label in sorted(CORE_LABEL_SET)
        if (slot := _label_slot(receipts, label))
    }
    mutable_fields = _summarize_mutable_fields(label_slots)
    return {
        "merchant_name": merchant_name,
        "receipt_count": len(receipts),
        "source_receipt_keys": [_receipt_key(receipt) for receipt in receipts],
        "word_count": _summary(
            [len(receipt["words"]) for receipt in receipts]
        ).to_dict(),
        "label_slots": label_slots,
        "mutable_fields": mutable_fields,
        "category_patterns": _summarize_categories(analyses),
        "tax_policy": _summarize_tax_policy(analyses, merchant_name),
        "real_structure_baseline": real_structure_baseline,
        "observed_item_catalog": [entry.to_dict() for entry in catalog[:25]],
        "synthesis_readiness": _build_synthesis_readiness(
            merchant_name,
            receipts=receipts,
            analyses=analyses,
            tax_analyses=all_analyses,
            catalog=catalog,
            label_slots=label_slots,
            mutable_fields=mutable_fields,
            online_catalog=online_catalog,
        ),
        "generation_limits": {
            "max_candidates_per_training_run": 5,
            "max_arithmetic_candidates_per_training_run": 1,
            "validation_split": "real_receipts_only",
        },
        "confidence_notes": [
            "Built from current structured receipt data at pattern-build time.",
            "Synthetic examples clone merchant-local token order and geometry.",
            "Observed item additions require catalog evidence from another receipt.",
        ],
    }


def build_merchant_synthesis_readiness(
    merchant_name: str,
    receipts_data: list[dict[str, Any]],
    *,
    plan: dict[str, Any] | None = None,
    online_catalog: list[OnlineCatalogEntry] | None = None,
) -> dict[str, Any] | None:
    """Score whether a merchant has enough local evidence to synthesize safely."""
    receipts = [
        receipt
        for receipt in (
            _normalize_receipt(receipt) for receipt in receipts_data
        )
        if receipt["words"]
    ]
    if not receipts:
        return None

    all_analyses = [_analyze_receipt(receipt) for receipt in receipts]
    analyses = [analysis for analysis in all_analyses if analysis.line_items]
    catalog = _build_item_catalog(analyses)
    label_slots = {
        label: slot
        for label in sorted(CORE_LABEL_SET)
        if (slot := _label_slot(receipts, label))
    }
    return _build_synthesis_readiness(
        merchant_name,
        receipts=receipts,
        analyses=analyses,
        tax_analyses=all_analyses,
        catalog=catalog,
        label_slots=label_slots,
        mutable_fields=_summarize_mutable_fields(label_slots),
        plan=plan,
        online_catalog=online_catalog,
    )


def generate_merchant_synthesis_candidates(
    plan: dict[str, Any],
    receipts_data: list[dict[str, Any]],
    *,
    max_candidates: int = 5,
    online_catalog: list[OnlineCatalogEntry] | None = None,
    font_geometry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate train-only candidates for any merchant with real receipts.

    When an online catalog is available for the merchant (registered in
    ``_MERCHANT_ONLINE_CATALOG`` or passed via ``online_catalog``), a share of
    the budget is spent on ``compose_online_catalog`` candidates: net-new
    receipts whose item rows are rendered from online products with labels we
    assign, giving clean item-region supervision a cloned real row cannot.

    ``font_geometry`` is an optional merchant font profile in synthesis pixel
    space — exactly ``MerchantFontProfile.to_geometry_params()`` from
    ``rendering.font_profile`` (built on PR #994). When provided it is stamped on
    every working receipt and used ONLY as a geometry fallback (char width, glyph
    height, row pitch, price column) for scaffolds too sparse to measure their
    own. Real receipt geometry always wins, so the structure-similarity gate is
    unchanged for receipts that can measure themselves.
    """
    merchant_name = str(plan.get("merchant_name") or "Unknown merchant")
    profile = build_merchant_synthesis_profile(
        merchant_name,
        receipts_data,
        online_catalog=online_catalog,
    )
    if profile is None:
        return []

    receipts = [
        receipt
        for receipt in (
            _normalize_receipt(receipt) for receipt in receipts_data
        )
        if receipt["words"]
    ]
    if font_geometry:
        for receipt in receipts:
            receipt["font_geometry"] = font_geometry
    analyses = [_analyze_receipt(receipt) for receipt in receipts]
    catalog = _build_item_catalog(
        [analysis for analysis in analyses if analysis.line_items]
    )
    profile["synthesis_readiness"] = _build_synthesis_readiness(
        merchant_name,
        receipts=receipts,
        analyses=[analysis for analysis in analyses if analysis.line_items],
        tax_analyses=analyses,
        catalog=catalog,
        label_slots=profile.get("label_slots") or {},
        mutable_fields=profile.get("mutable_fields") or {},
        plan=plan,
        online_catalog=online_catalog,
    )

    candidates: list[dict[str, Any]] = []

    # Grounded add-item augmentations are the highest-value train-only signal and
    # the curated batch is intended to be grounded-dominant (the bundle gate's
    # default grounded-share floor is 0.5). Generate them FIRST and allow several
    # from a rich cross-receipt catalog, before filling the remaining budget with
    # the other operations.
    add_target = max(1, (max_candidates + 1) // 2)
    candidates.extend(
        _generate_add_item_candidates(
            merchant_name,
            profile,
            analyses,
            catalog,
            start_index=len(candidates) + 1,
            limit=add_target,
        )
    )

    # Online-catalog template fill: high-value clean-supervision candidates that
    # exist only for merchants with a registered/injected online catalog, so all
    # other merchants are unaffected. Cap its share so the grounded add-items,
    # arithmetic, field replacements, and hard negatives still get budget.
    catalog_entries = [
        entry
        for entry in _merchant_online_catalog(merchant_name, online_catalog)
        # Skip entries whose name abbreviates to nothing renderable (all-numeric
        # / stopwords) — they would compose a nameless item row.
        if _abbrev_product_name(entry.name)
    ]
    if catalog_entries and len(candidates) < max_candidates:
        compose_limit = min(
            max_candidates - len(candidates),
            max(1, max_candidates // 3),
        )
        candidates.extend(
            _generate_compose_online_catalog_candidates(
                merchant_name,
                profile,
                analyses,
                catalog_entries,
                start_index=len(candidates) + 1,
                limit=compose_limit,
            )
        )

    # Places-driven store-header diversity: swap the address/phone cluster for a
    # different cached branch of the same merchant. Only fires for merchants with
    # >=2 complete cached store locations; capped so it cannot crowd out the
    # grounded item edits.
    if len(candidates) < max_candidates:
        header_limit = min(
            max_candidates - len(candidates),
            max(1, max_candidates // 4),
        )
        candidates.extend(
            _generate_compose_store_header_candidates(
                merchant_name,
                profile,
                receipts,
                analyses,
                start_index=len(candidates) + 1,
                limit=header_limit,
            )
        )

    if len(candidates) < max_candidates:
        arithmetic = _generate_remove_item_candidate(
            merchant_name,
            profile,
            analyses,
            index=len(candidates) + 1,
        )
        if arithmetic:
            candidates.append(arithmetic)

    # A dedicated taxable removal (distinct index) so taxable-edit supervision
    # surfaces even when the best overall removal is a non-taxable item.
    if len(candidates) < max_candidates:
        taxable_removal = _generate_remove_item_candidate(
            merchant_name,
            profile,
            analyses,
            index=len(candidates) + 1,
            prefer_taxable=True,
        )
        existing_removals = {
            _removed_item_identity(candidate)
            for candidate in candidates
            if _candidate_operation(candidate) == "remove_line_item"
        }
        if (
            taxable_removal
            and _removed_item_identity(taxable_removal) not in existing_removals
        ):
            candidates.append(taxable_removal)

    for label in ("DATE", "TIME"):
        if len(candidates) >= max_candidates:
            break
        replacement = _generate_mutable_field_candidate(
            merchant_name,
            profile,
            receipts,
            analyses,
            label=label,
            index=len(candidates) + 1,
        )
        if replacement:
            candidates.append(replacement)

    for label in _SCRUBBABLE_LABELS:
        if len(candidates) >= max_candidates:
            break
        scrub = _generate_value_scrub_candidate(
            merchant_name,
            profile,
            receipts,
            analyses,
            label=label,
            index=len(candidates) + 1,
        )
        if scrub:
            candidates.append(scrub)

    # Hard negatives are the lowest-priority, fill-last operation: they only
    # consume budget the grounded edits / field replacements did not use.
    for recipe in _false_positive_recipes(plan):
        if len(candidates) >= max_candidates:
            break
        candidate = _generate_hard_negative_candidate(
            merchant_name,
            profile,
            receipts,
            analyses,
            recipe,
            index=len(candidates) + 1,
        )
        if candidate:
            candidates.append(candidate)

    # Reject totals-owning candidates whose printed cascade still contradicts
    # itself (a stray un-summed LINE_TOTAL from a noisy base, or a subtotal/tax/
    # grand-total that does not reconcile). The item count is always repaired in
    # _candidate_from_receipt, so only the arithmetic operations that recompute
    # the money cascade can fail here; field-replacement / hard-negative / header
    # operations inherit the base totals untouched and are left as-is.
    candidates = [
        candidate
        for candidate in candidates
        if _candidate_totals_reconcile(candidate)
    ]

    return candidates[:max_candidates]


# Operations that recompute the SUBTOTAL/TAX/GRAND_TOTAL cascade and so must emit
# a self-consistent one; others inherit the base receipt's totals unchanged.
_TOTALS_OWNING_OPERATIONS = {
    "add_line_item",
    "remove_line_item",
    "compose_online_catalog",
}


def _candidate_totals_reconcile(candidate: dict[str, Any]) -> bool:
    """True unless a totals-owning candidate's content cascade fails to reconcile."""
    metadata = candidate.get("metadata") or {}
    if metadata.get("operation") not in _TOTALS_OWNING_OPERATIONS:
        return True
    report = metadata.get("content_reconciliation") or {}
    # Absent report (older path) is treated as reconciling so we never over-drop.
    return bool(report.get("reconciles", True))


def _false_positive_recipes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    recipes = [
        recipe
        for recipe in plan.get("recipes", [])
        if recipe.get("error_kind") == "false_positive"
        and str(recipe.get("actual_label") or "").upper() == "O"
    ]
    recipes.sort(key=lambda recipe: str(recipe.get("predicted_label") or ""))
    return recipes


def _build_synthesis_readiness(
    merchant_name: str,
    *,
    receipts: list[dict[str, Any]],
    analyses: list[MerchantAnalysis],
    tax_analyses: list[MerchantAnalysis] | None = None,
    catalog: list[MerchantCatalogEntry],
    label_slots: dict[str, Any],
    mutable_fields: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    online_catalog: list[OnlineCatalogEntry] | None = None,
) -> dict[str, Any]:
    """Build a compact offline readiness score for merchant-local synthesis."""
    mutable_fields = mutable_fields or {}
    tax_analyses = tax_analyses or analyses
    tax_policy = _summarize_tax_policy(analyses, merchant_name)
    add_plans = _build_add_item_plans(merchant_name, analyses, catalog)
    remove_plans = _build_remove_item_plans(merchant_name, analyses)
    false_positive_recipes = _false_positive_recipes(plan or {})
    ready_hard_negative_labels = _hard_negative_ready_labels(
        false_positive_recipes,
        label_slots,
    )
    potential_hard_negative_count = (
        len(ready_hard_negative_labels)
        if false_positive_recipes
        else min(3, len(label_slots))
    )

    category_values = sorted(
        {
            item.category
            for analysis in analyses
            for item in analysis.line_items
            if item.category != UNKNOWN_CATEGORY
        }
    )
    receipts_with_grand_total = sum(
        1 for analysis in analyses if analysis.grand_total is not None
    )
    receipts_with_subtotal = sum(
        1 for analysis in analyses if analysis.subtotal is not None
    )
    receipts_with_categories = sum(
        1 for analysis in analyses if analysis.category_sequence
    )
    cross_receipt_catalog_items = [
        entry for entry in catalog if len(entry.source_receipt_keys) > 1
    ]
    supported_operations = []
    if potential_hard_negative_count:
        supported_operations.append("hard_negative")
    if add_plans:
        supported_operations.append("add_line_item")
    if remove_plans:
        supported_operations.append("remove_line_item")
    if any(
        field.get("safe_to_mutate") is True
        for field in mutable_fields.values()
    ):
        supported_operations.append("replace_field")
    mutable_field_count = sum(
        1
        for field in mutable_fields.values()
        if field.get("safe_to_mutate") is True
    )
    # Online-catalog template fill is supported when the merchant has a
    # registered/injected online catalog, a stable observed tax rate (to put a
    # realistic, consistent tax on a composed receipt), and at least one usable
    # scaffold. Without it, composed candidates have no contract and are rejected
    # by the loader's contract gate.
    online_catalog_entries = _merchant_online_catalog(merchant_name, online_catalog)
    compose_rate, compose_rate_stable, _ = _stable_tax_rate(tax_analyses)
    compose_scaffolds = [
        analysis
        for analysis in analyses
        if analysis.line_items
        and analysis.grand_total is not None
        and analysis.subtotal is not None
    ]
    compose_online_catalog_ready = bool(
        len(online_catalog_entries) >= 2
        and compose_rate is not None
        and compose_rate_stable
        and compose_scaffolds
    )
    if compose_online_catalog_ready:
        supported_operations.append("compose_online_catalog")
    compose_online_catalog_capacity = (
        len(online_catalog_entries) if compose_online_catalog_ready else 0
    )

    # Places-driven store-header diversity is supported when the cache holds at
    # least two complete store locations for this merchant (one to compose onto,
    # at least one *other* branch to source from).
    store_place_records: list[dict[str, Any]] = []
    for receipt in receipts:
        pool_records = receipt.get("merchant_place_pool")
        if isinstance(pool_records, list) and pool_records:
            store_place_records = [r for r in pool_records if isinstance(r, dict)]
            break
    if not store_place_records:
        store_place_records = [
            receipt.get("receipt_place")
            for receipt in receipts
            if isinstance(receipt.get("receipt_place"), dict)
        ]
    # Scope to this merchant so a mixed-merchant payload cannot over-count
    # locations and wrongly mark compose_store_header supported.
    store_place_records = [
        record
        for record in store_place_records
        if isinstance(record, dict)
        and _store_merchant_match(
            merchant_name, str(record.get("merchant_name") or "")
        )
    ]
    store_profiles = extract_store_profiles(store_place_records)
    store_header_location_count = len(
        [profile for profile in store_profiles if profile.is_complete()]
    )
    compose_store_header_capacity = (
        store_header_location_count if store_header_location_count >= 2 else 0
    )
    if compose_store_header_capacity:
        supported_operations.append("compose_store_header")

    blockers: list[str] = []
    limitations: list[str] = []
    if not receipts:
        blockers.append("no_receipts")
    if not analyses:
        blockers.append("no_line_items")
    if not catalog and not compose_online_catalog_ready:
        blockers.append("no_observed_item_catalog")
    elif not catalog:
        limitations.append("no_observed_item_catalog")
    if not supported_operations:
        blockers.append("no_supported_operations")
    if false_positive_recipes and not ready_hard_negative_labels:
        limitations.append("plan_has_no_supported_hard_negative_slots")
    if not add_plans:
        limitations.append("no_cross_receipt_grounded_add_items")
    if not remove_plans:
        limitations.append("no_removable_non_taxable_items")
    if not receipts_with_grand_total:
        limitations.append("no_grand_total_anchors")
    if not category_values:
        limitations.append("no_category_structure")
    if _safe_int(tax_policy.get("taxable_item_count")) and not tax_policy.get(
        "tax_changing_synthesis_ready"
    ):
        limitations.append("tax_changing_synthesis_not_enabled")

    score_components = {
        "receipt_depth": min(1.0, len(receipts) / 3.0),
        "line_item_depth": min(1.0, len(analyses) / 2.0),
        "catalog_grounding": min(
            1.0,
            max(len(catalog), compose_online_catalog_capacity) / 4.0,
        ),
        "category_structure": 1.0 if category_values else 0.0,
        "summary_anchors": min(1.0, receipts_with_grand_total / 2.0),
        "operation_support": min(1.0, len(supported_operations) / 2.0),
        "label_geometry": min(1.0, len(label_slots) / 4.0),
    }
    weights = {
        "receipt_depth": 0.12,
        "line_item_depth": 0.16,
        "catalog_grounding": 0.18,
        "category_structure": 0.14,
        "summary_anchors": 0.16,
        "operation_support": 0.18,
        "label_geometry": 0.06,
    }
    score = round(
        sum(score_components[key] * weights[key] for key in weights),
        3,
    )
    status = "ready"
    if blockers or score < 0.4:
        status = "blocked"
    elif score < 0.7:
        status = "partial"

    return {
        "merchant_name": merchant_name,
        "status": status,
        "score": score,
        "score_components": {
            key: round(value, 3) for key, value in score_components.items()
        },
        "supported_operations": supported_operations,
        "candidate_capacity": min(
            5,
            potential_hard_negative_count
            + len(add_plans)
            + len(remove_plans)
            + mutable_field_count
            + compose_online_catalog_capacity
            + compose_store_header_capacity,
        ),
        "hard_negative_label_count": potential_hard_negative_count,
        "ready_hard_negative_labels": ready_hard_negative_labels,
        "grounded_add_item_candidate_count": len(add_plans),
        "removable_item_candidate_count": len(remove_plans),
        "mutable_field_count": mutable_field_count,
        "compose_online_catalog_candidate_count": compose_online_catalog_capacity,
        "compose_store_header_location_count": store_header_location_count,
        "compose_store_header_candidate_count": compose_store_header_capacity,
        "mutable_fields": {
            label: field
            for label, field in mutable_fields.items()
            if field.get("safe_to_mutate") is True
        },
        "tax_policy": tax_policy,
        "receipt_count": len(receipts),
        "analyzed_receipt_count": len(analyses),
        "line_item_count": sum(
            len(analysis.line_items) for analysis in analyses
        ),
        "catalog_item_count": len(catalog),
        "cross_receipt_catalog_item_count": len(cross_receipt_catalog_items),
        "category_count": len(category_values),
        "receipts_with_grand_total": receipts_with_grand_total,
        "receipts_with_subtotal": receipts_with_subtotal,
        "receipts_with_categories": receipts_with_categories,
        "blockers": blockers,
        "limitations": limitations,
        "grounded_add_item_examples": [
            _catalog_readiness_example(entry)
            for _score, _analysis, entry, _y_center in add_plans[:5]
        ],
    }


def _hard_negative_ready_labels(
    recipes: list[dict[str, Any]],
    label_slots: dict[str, Any],
) -> list[str]:
    labels = []
    for recipe in recipes:
        label = _normalize_label(recipe.get("predicted_label"))
        if label != "O" and label in label_slots and label not in labels:
            labels.append(label)
    return labels


def _catalog_readiness_example(entry: MerchantCatalogEntry) -> dict[str, Any]:
    return {
        "product_text": entry.product_text,
        "category": entry.category,
        "line_total": _format_money(entry.amount),
        "observed_count": entry.count,
        "source_receipt_count": len(entry.source_receipt_keys),
    }


def _generate_hard_negative_candidate(
    merchant_name: str,
    profile: dict[str, Any],
    receipts: list[dict[str, Any]],
    analyses: list[MerchantAnalysis],
    recipe: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any] | None:
    predicted_label = _normalize_label(recipe.get("predicted_label"))
    slot = profile.get("label_slots", {}).get(predicted_label)
    if not slot:
        return None

    base = _choose_base_receipt(receipts, used=index - 1)
    mutated = copy.deepcopy(base)
    tokens = _hard_negative_tokens(predicted_label)
    x0 = max(25, min(900, int(round(slot["x"]["p50"] - 60))))
    y0 = _nearest_open_y(mutated, x0, int(round(slot["y"]["p50"])), tokens)
    if y0 is None:
        # No clean gap near the target zone; placing the distractor anyway would
        # overlap real words and fail the layout-integrity gate. Skip instead.
        return None
    _insert_line_sorted(mutated, _build_line(tokens, [], x0=x0, y0=y0))
    row = _candidate_from_receipt(
        mutated,
        merchant_name,
        source="merchant_parameterized_geometry",
        operation="hard_negative",
        index=index,
        metadata={
            "profile": _profile_summary(profile),
            "base_receipt_key": _receipt_key(base),
            "actual_label": "O",
            "predicted_label": predicted_label,
            "error_kind": "false_positive",
            "mutation": "merchant_local_hard_negative",
            "target_zone": recipe.get("target_zone") or {},
            "structure_similarity": _score_structure_similarity(
                _analyze_receipt(mutated),
                analyses,
            ),
            "expected_label_effect": (
                f"Improve {predicted_label} precision with a merchant-local O lookalike."
            ),
        },
    )
    return row if len(row["tokens"]) <= 220 else None


def _generate_add_item_candidate(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    catalog: list[MerchantCatalogEntry],
    *,
    index: int,
) -> dict[str, Any] | None:
    plans = _build_add_item_plans(merchant_name, analyses, catalog)
    if not plans:
        return None

    candidates = []
    for plan_rank, (plan_score, analysis, entry, y_center) in enumerate(
        plans,
        start=1,
    ):
        candidate = _build_add_item_candidate_from_plan(
            merchant_name,
            profile,
            analyses,
            analysis,
            entry,
            y_center,
            index=index,
            plan_rank=plan_rank,
            plan_count=len(plans),
            plan_score=plan_score,
        )
        if candidate:
            candidates.append(candidate)
    return select_high_fidelity_synthesis_candidate(candidates)


def _generate_add_item_candidates(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    catalog: list[MerchantCatalogEntry],
    *,
    start_index: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Generate up to ``limit`` distinct grounded add-item candidates.

    Grounded add-item augmentations are the highest-value train-only signal, and
    a merchant with a rich cross-receipt catalog can support several. Candidates
    are de-duplicated by (category, product) and ranked by the same fidelity key
    the selection gate uses, so the strongest distinct grounded edits come first.
    """
    if limit <= 0:
        return []
    plans = _build_add_item_plans(merchant_name, analyses, catalog)
    if not plans:
        return []
    # Keep the HIGHEST-fidelity candidate per (category, product) instead of the
    # first one built. Whether a plan yields a high-fidelity candidate depends on
    # the chosen base's geometry (layout can fail on one base but pass another),
    # so trying only the first plan for a key could discard the one valid
    # duplicate and let the bundle gate reject the merchant's only add-item. A
    # small per-key build cap bounds the extra work.
    max_plans_per_key = 3
    built_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    attempts_by_key: dict[tuple[str, str], int] = {}
    for plan_rank, (plan_score, analysis, entry, y_center) in enumerate(
        plans, start=1
    ):
        key = (entry.category, _normalize_product_text(entry.product_text))
        if attempts_by_key.get(key, 0) >= max_plans_per_key:
            continue
        attempts_by_key[key] = attempts_by_key.get(key, 0) + 1
        candidate = _build_add_item_candidate_from_plan(
            merchant_name,
            profile,
            analyses,
            analysis,
            entry,
            y_center,
            index=start_index,
            plan_rank=plan_rank,
            plan_count=len(plans),
            plan_score=plan_score,
        )
        if not candidate:
            continue
        existing = built_by_key.get(key)
        if existing is None or _candidate_selection_key(
            candidate
        ) > _candidate_selection_key(existing):
            built_by_key[key] = candidate
    built: list[dict[str, Any]] = list(built_by_key.values())
    # Rank high-fidelity grounded add-items first so the strongest distinct
    # options lead; the bundle's acceptance gate drops any that are not
    # high-fidelity from the final curated batch.
    indexed = sorted(
        enumerate(built),
        key=lambda item: (
            _candidate_is_high_fidelity(item[1]),
            *_candidate_selection_key(item[1]),
            -item[0],
        ),
        reverse=True,
    )
    chosen = []
    # Renumber so candidate ids / indices stay sequential within the batch, and
    # record the same selection evidence the single-pick selector emits.
    for offset, (input_index, candidate) in enumerate(indexed[:limit]):
        _renumber_candidate(candidate, merchant_name, start_index + offset)
        _set_selection_evidence(
            candidate,
            candidate_count=len(built),
            selected_index=input_index,
        )
        chosen.append(candidate)
    return chosen


def _candidate_is_high_fidelity(candidate: dict[str, Any]) -> bool:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    quality = metadata.get("candidate_quality")
    quality = quality if isinstance(quality, dict) else {}
    return quality.get("high_fidelity") is True


def _candidate_operation(candidate: dict[str, Any]) -> str:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return str(metadata.get("operation") or "").strip()


def _removed_item_identity(candidate: dict[str, Any]) -> tuple[str, str, str, bool | None]:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    removed = metadata.get("removed_item")
    removed = removed if isinstance(removed, dict) else {}
    taxable = removed.get("taxable")
    return (
        str(metadata.get("base_receipt_key") or ""),
        _normalize_product_text(removed.get("product_text") or ""),
        str(removed.get("line_total") or ""),
        taxable if taxable in (True, False) else None,
    )


def _renumber_candidate(
    candidate: dict[str, Any], merchant_name: str, index: int
) -> None:
    metadata = candidate.get("metadata") or {}
    source = str(metadata.get("source") or "merchant_arithmetic_geometry")
    operation = str(metadata.get("operation") or "add_line_item")
    slug = _slug(f"{merchant_name}-{source}-{operation}-{index}")
    candidate["candidate_id"] = slug
    candidate["receipt_key"] = f"synthetic-{slug}#00001"
    candidate["image_id"] = f"synthetic-{slug}"


def _select_source_row(
    entry: MerchantCatalogEntry, *, exclude_key: str
) -> dict[str, Any] | None:
    """A captured real row group from a receipt OTHER than the base."""
    for key, captured in (entry.source_rows or {}).items():
        if key != exclude_key and captured.get("lines"):
            return captured
    return None


def _clone_row_group_lines(
    captured: dict[str, Any], *, y_center: float
) -> list[dict[str, Any]]:
    """Transplant a captured real row group to ``y_center`` in the base receipt.

    Each word keeps its original x position and the group keeps its internal
    vertical spacing (so a code/name/flag/price layout stays intact); only the
    group is re-anchored vertically. Labels are preserved verbatim so the
    product code and tax flag train as ``O`` and the name/price keep their tags.
    """
    ordered = sorted(
        captured.get("lines") or [],
        key=lambda line: -float(line.get("y") or 0.0),  # y-high-is-top
    )
    if not ordered:
        return []
    top_y = float(ordered[0].get("y") or 0.0)
    out: list[dict[str, Any]] = []
    for offset, src in enumerate(ordered):
        drop = int(round((top_y - float(src.get("y") or 0.0)) * 1000))
        line_top = max(0, min(976, int(round(y_center)) - 12 - drop))
        words = []
        for word_index, word in enumerate(src.get("words") or [], start=1):
            box = word.get("bbox") or [0, 0, 0, 0]
            height = max(1, int(box[3]) - int(box[1]))
            words.append(
                {
                    "text": word.get("text", ""),
                    "bbox": [
                        int(box[0]),
                        line_top,
                        int(box[2]),
                        line_top + height,
                    ],
                    "labels": list(word.get("labels") or []),
                    "line_id": 20_000 + offset,
                    "word_id": word_index,
                }
            )
        out.append(
            {"line_id": 20_000 + offset, "y": line_top / 1000, "words": words}
        )
    return out


def _row_group_height(
    group_lines: list[dict[str, Any]], *, fallback: int
) -> int:
    tops: list[int] = []
    bottoms: list[int] = []
    for line in group_lines:
        for word in line.get("words") or []:
            box = word.get("bbox") or [0, 0, 0, 0]
            tops.append(int(box[1]))
            bottoms.append(int(box[3]))
    if not tops:
        return fallback
    return max(fallback, (max(bottoms) - min(tops)) + 8)


def _estimate_row_slope(line: dict[str, Any]) -> float:
    """Slope (dy/dx) of a row's word centroids — the receipt's local tilt from
    being photographed at an angle. Used to re-apply the same tilt to an
    inserted row so it visually matches its neighbors."""
    boxes = [
        word.get("bbox")
        for word in line.get("words") or []
        if word.get("bbox")
    ]
    if len(boxes) < 2:
        return 0.0
    xs = [_cx(box) for box in boxes]
    ys = [_cy(box) for box in boxes]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum(
        (xs[i] - mean_x) * (ys[i] - mean_y) for i in range(len(xs))
    ) / denom
    # Real receipt tilt is gentle; bound it so the re-applied skew stays small.
    return max(-0.05, min(0.05, slope))


def _apply_row_slope(
    lines: list[dict[str, Any]], slope: float, *, ref_x: float
) -> None:
    """Tilt each word's box by ``slope`` about ``ref_x`` (re-apply receipt skew)."""
    if not slope:
        return
    for line in lines:
        for word in line.get("words") or []:
            box = word.get("bbox")
            if not box:
                continue
            dy = int(round(slope * (_cx(box) - ref_x)))
            box[1] += dy
            box[3] += dy


def _anchor_band_top(
    band_lines: list[dict[str, Any]], *, target_top: float
) -> None:
    """Translate a band so its HIGHEST point (after any tilt) sits at
    ``target_top`` — keeping every tilted word below the boundary row above."""
    tops = [
        word["bbox"][3]
        for line in band_lines
        for word in line.get("words") or []
        if word.get("bbox")
    ]
    if not tops:
        return
    dy = int(round(target_top - max(tops)))
    if not dy:
        return
    for line in band_lines:
        for word in line.get("words") or []:
            box = word.get("bbox")
            if box:
                box[1] = max(0, box[1] + dy)
                box[3] = max(0, box[3] + dy)


def _reflow_insert_lines(
    receipt: dict[str, Any],
    *,
    after_index: int,
    band_lines: list[dict[str, Any]],
    reserve: int,
) -> dict[str, int]:
    """Splice ``band_lines`` into the row sequence after ``after_index`` and push
    every LOWER row down by ``reserve``.

    The shift is by ROW INDEX, not a y threshold: every word of a lower row
    moves together as a rigid block, so a slanted row can never be half-shifted
    and clipped by the inserted band. The new band drops into the reserved gap.
    """
    lines = receipt.setdefault("lines", [])
    after_index = max(-1, min(after_index, len(lines) - 1))
    below = lines[after_index + 1 :]
    # Open a gap by pushing the lower rows down by INDEX (rigid blocks).
    for line in below:
        for word in line.get("words") or []:
            box = word.get("bbox")
            if box:
                box[1] -= reserve
                box[3] -= reserve
        if isinstance(line.get("y"), (int, float)):
            line["y"] -= reserve / 1000
    lines[after_index + 1 : after_index + 1] = band_lines
    # Compress the whole receipt back onto the canvas so the gap never pushes the
    # footer (or any row) off the page into a degenerate/out-of-bounds box. A
    # uniform fit preserves relative layout, so it introduces no new overlaps.
    _fit_receipt_to_canvas(receipt)
    _refresh_words(receipt)
    return {
        "line_count": len(below),
        "median_shift": reserve,
        "min_shift": reserve,
        "max_shift": reserve,
    }


def _fit_receipt_to_canvas(
    receipt: dict[str, Any], *, bottom: int = 4, top: int = 996
) -> None:
    """Uniformly scale/translate all word boxes so the receipt's content fits
    within [bottom, top] — never off-canvas, never zero-height."""
    boxes = [
        word["bbox"]
        for line in receipt.get("lines", [])
        for word in line.get("words", [])
        if word.get("bbox")
    ]
    if not boxes:
        return
    lo = min(box[1] for box in boxes)
    hi = max(box[3] for box in boxes)
    span = hi - lo
    if span <= 0:
        return
    scale = min(1.0, (top - bottom) / span)
    if lo >= bottom and hi <= top and scale >= 1.0:
        return
    for box in boxes:
        box[1] = int(round(bottom + (box[1] - lo) * scale))
        box[3] = int(round(bottom + (box[3] - lo) * scale))
        if box[3] <= box[1]:
            box[3] = box[1] + 1


def _reflow_remove_lines(
    receipt: dict[str, Any],
    *,
    band_indices: list[int],
    line_step: int,
) -> dict[str, int]:
    """Delete the rows at ``band_indices`` and pull every visually LOWER row up to
    close the vacated gap.

    Coordinate model is y-high-is-top: ``box[3]`` is a word's TOP edge, ``box[1]``
    its BOTTOM edge, and a LARGER y sits higher on the receipt. Removing the band
    leaves a hole; every row that sits BELOW the band (smaller y) must move UP
    (larger y) by exactly the band's vacated height so the receipt closes up
    without inverting reading order.

    Rows to move are chosen by Y-POSITION (not array index), so the close-up is
    robust even when the lines array is not stored top-to-bottom. The shift is the
    gap between the band's top and the nearest lower row's top, which equals the
    removed band's vertical extent; because every lower row only moves up toward
    where the band already sat (never above the band's old top), no row can run
    off the top edge, so the shift is applied with NO 1000 clamp — the old clamp
    is what collapsed near-top rows onto y=1000 and scrambled the order.
    """
    lines = receipt.setdefault("lines", [])
    band = sorted(i for i in set(band_indices) if 0 <= i < len(lines))
    if not band:
        return {"line_count": 0, "median_shift": 0, "min_shift": 0, "max_shift": 0}

    band_set = set(band)
    band_lines = [lines[i] for i in band]

    # Band's vertical extent from its words (y-high-is-top).
    tops: list[int] = []
    bottoms: list[int] = []
    for line in band_lines:
        for word in line.get("words") or []:
            box = word.get("bbox")
            if box:
                tops.append(box[3])
                bottoms.append(box[1])

    def _delete_band_and_normalize() -> None:
        for i in sorted(band_set, reverse=True):
            del lines[i]
        _normalize_word_boxes(receipt)
        _refresh_words(receipt)

    if not tops:
        # Empty band occupies no visual space; nothing below it needs to move.
        _delete_band_and_normalize()
        return {"line_count": 0, "median_shift": 0, "min_shift": 0, "max_shift": 0}

    band_top = max(tops)
    band_bottom = min(bottoms)
    band_center = (band_top + band_bottom) / 2.0

    # Classify the remaining rows by VISUAL position: a row is "below" the band
    # when its center sits lower (smaller y) than the band's center. This splits
    # cleanly because the band is a contiguous vertical slot — the nearest lower
    # row's center is below band_bottom and the nearest upper row's center is
    # above band_top.
    below: list[tuple[float, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        if index in band_set:
            continue
        boxes = [w.get("bbox") for w in (line.get("words") or []) if w.get("bbox")]
        if not boxes:
            continue
        center_y = sum((b[1] + b[3]) / 2.0 for b in boxes) / len(boxes)
        if center_y < band_center:
            below.append((max(b[3] for b in boxes), line))

    if not below:
        # Band is at the very bottom — no lower rows to pull up.
        _delete_band_and_normalize()
        return {"line_count": 0, "median_shift": 0, "min_shift": 0, "max_shift": 0}

    # The nearest lower row is the one whose top edge is highest among the lower
    # rows. Pulling its top up to where the band's top was closes exactly the
    # band's vacated extent; every lower row shifts up by that same amount.
    next_top = max(top for top, _ in below)
    shift = int(round(band_top - next_top))
    if shift <= 0:
        # Degenerate overlap; do not push rows down (that would open a gap or
        # invert order). Just drop the band.
        _delete_band_and_normalize()
        return {"line_count": 0, "median_shift": 0, "min_shift": 0, "max_shift": 0}

    shifted = 0
    for _, line in below:
        for word in line.get("words") or []:
            box = word.get("bbox")
            if box:
                # Preserve x exactly; only translate vertically (no clamp — the
                # row only rises toward the band's old slot, never off-canvas).
                box[1] += shift
                box[3] += shift
        shifted += 1

    # Drop the band rows, normalize any inverted boxes, then re-derive each line's
    # y from its (now shifted) words.
    for i in sorted(band_set, reverse=True):
        del lines[i]
    _normalize_word_boxes(receipt)
    for line in lines:
        if line.get("words"):
            line["y"] = _line_y(line)
    _refresh_words(receipt)
    return {
        "line_count": shifted,
        "median_shift": shift,
        "min_shift": shift,
        "max_shift": shift,
    }


def _normalize_word_boxes(receipt: dict[str, Any]) -> None:
    """Canonicalize every word bbox so x0 <= x1 and y0 <= y1.

    Geometry edits can leave inverted boxes (y0 > y1 or x0 > x1) that both render
    wrong and poison training geometry. Swap the offending pair in place so each
    box has a non-negative width and height.
    """
    for line in receipt.get("lines", []):
        for word in line.get("words") or []:
            box = word.get("bbox")
            if not box or len(box) < 4:
                continue
            if box[0] > box[2]:
                box[0], box[2] = box[2], box[0]
            if box[1] > box[3]:
                box[1], box[3] = box[3], box[1]


def _item_region_floor_y(receipt: dict[str, Any]) -> float | None:
    """Bottom edge (lowest y, y-high-is-top) of the REAL line-item region — the
    top edge of the lowest real PRODUCT_NAME / LINE_TOTAL / QUANTITY /
    UNIT_PRICE word.

    Derived from the receipt itself (not a pre-reflow analysis) so it stays
    valid after the add-item reflow rescales every box. Synthetic inserted rows
    (line_id >= the synthetic base) are excluded so a wrongly placed insert can
    never lower the floor and hide the genuine footer summary from the gate.
    """
    item_labels = {"PRODUCT_NAME", "LINE_TOTAL", "QUANTITY", "UNIT_PRICE"}
    tops: list[int] = []
    for line in receipt.get("lines", []):
        if _is_synthetic_line_id(line.get("line_id")):
            continue
        for word in line.get("words", []):
            box = word.get("bbox")
            if not box or _is_synthetic_line_id(word.get("line_id")):
                continue
            if set(word.get("labels") or []) & item_labels:
                tops.append(box[1])
    return float(min(tops)) if tops else None


def _summary_block_top_y(receipt: dict[str, Any]) -> float | None:
    """Top edge (highest y) of the SUBTOTAL/TAX/TOTAL summary block — the line
    above which all item rows must sit (y-high-is-top).

    The genuine summary block sits BELOW the line items (lower y). Receipts
    routinely carry a stray summary-labeled word ABOVE the items too — a header
    "balance", a repeated total, or a duplicate-OCR'd grand total — and a naive
    ``max`` over every labeled word would latch onto that stray top total and
    misread the whole item region as sitting below the summary. Restrict the
    block to summary words at or below the item region's floor so a stray total
    above the items can never define the boundary.
    """
    tolerance = 4.0
    item_floor_y = _item_region_floor_y(receipt)
    ys: list[int] = []
    for line in receipt.get("lines", []):
        labels = {
            label
            for word in line.get("words", [])
            for label in (word.get("labels") or [])
        }
        if not (labels & {"SUBTOTAL", "TAX", "GRAND_TOTAL"}):
            continue
        for word in line.get("words", []):
            box = word.get("bbox")
            if not box:
                continue
            if (
                item_floor_y is not None
                and float(box[3]) > item_floor_y + tolerance
            ):
                # Word sits above the lowest item — a stray header total, not
                # the footer summary block. Skip it.
                continue
            ys.append(box[3])
    return float(max(ys)) if ys else None


def _build_add_item_candidate_from_plan(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    analysis: MerchantAnalysis,
    entry: MerchantCatalogEntry,
    y_center: float,
    *,
    index: int,
    plan_rank: int,
    plan_count: int,
    plan_score: float,
) -> dict[str, Any] | None:
    receipt = copy.deepcopy(analysis.receipt)
    old_total = analysis.grand_total
    if old_total is None:
        return None
    base_key = _receipt_key(analysis.receipt)
    line_step = _line_step(
        analysis.line_items,
        analysis.receipt,
        allow_font_geometry_fallback=True,
    )
    insertion_context = _category_insertion_context(analysis, entry.category, y_center)

    # Row-order reflow (no free-floating placement): insert the new band right
    # AFTER the lowest item of the target category in the row sequence, push the
    # lower rows down by INDEX, and re-apply the boundary row's tilt so the
    # inserted row stacks cleanly and matches the receipt's skew.
    gap = max(6, line_step // 3)
    category_items = [
        item for item in analysis.line_items if item.category == entry.category
    ]
    boundary_item = min(
        category_items or analysis.line_items,
        key=lambda item: item.band_bottom_y or item.center_y,
    )
    after_index = max(
        boundary_item.band_line_indices or boundary_item.line_indices
    )
    boundary_line = receipt.get("lines", [])[after_index]
    boundary_bottom = boundary_item.band_bottom_y or boundary_item.center_y
    slope = _estimate_row_slope(boundary_line)
    ref_x = (
        statistics.mean(
            _cx(word["bbox"])
            for word in boundary_line.get("words", [])
            if word.get("bbox")
        )
        if boundary_line.get("words")
        else 500.0
    )
    band_top_anchor = max(12.0, boundary_bottom - gap + 12)

    captured = _select_source_row(entry, exclude_key=base_key)
    sub_line_added = False
    if captured:
        # A real captured row group already carries the merchant's full row
        # grammar (marker, sub-line, exact tokens), so transplant it verbatim.
        delta_amount = _parse_money(captured.get("amount")) or entry.amount
        band_lines = _clone_row_group_lines(captured, y_center=band_top_anchor)
        row_cloned = True
    else:
        # Synthetic fallback row: render the merchant's item-line grammar (NOW
        # marker + optional SALE/WAS sub-line) so the rebuilt row matches format.
        delta_amount = entry.amount
        template = _item_line_template_for_merchant(
            _merchant_research_slug(merchant_name)
        )
        band_lines = _build_line_item_band(
            receipt,
            entry,
            y_center=band_top_anchor,
            merchant_name=merchant_name,
            template=template,
            line_step=line_step,
        )
        row_cloned = False
        sub_line_added = len(band_lines) > 1
    # De-glue BEFORE tilt/anchor/reflow so the integrity gate below scores the
    # final geometry: guarantee a blank cell after every price/flag/sale amount
    # so a cloned real row's tight "$4.39F" (or a sale "$7.49each") does not
    # render glued once the fixed-pitch renderer snaps it to the grid.
    _ensure_amount_word_gaps(
        band_lines, cell_w=_render_cell_width(analysis.receipt)
    )
    _apply_row_slope(band_lines, slope, ref_x=ref_x)
    # Re-anchor AFTER tilting so the band's highest tilted word still sits a gap
    # below the boundary row — the tilt can never push a word up into it.
    _anchor_band_top(band_lines, target_top=boundary_bottom - gap)
    shift_summary = _reflow_insert_lines(
        receipt,
        after_index=after_index,
        band_lines=band_lines,
        reserve=_row_group_height(band_lines, fallback=line_step) + gap,
    )
    # A real item row precedes the SUBTOTAL/TOTAL block. If the chosen boundary
    # item sat below that block (a mis-categorized item), the insert lands in the
    # footer — flag it so the quality gate rejects the candidate.
    summary_top = _summary_block_top_y(receipt)
    inserted_bottoms = [
        word["bbox"][1]
        for cloned_line in band_lines
        for word in cloned_line.get("words", [])
        if word.get("bbox")
    ]
    insertion_position_valid = summary_top is None or (
        min(inserted_bottoms, default=0) >= summary_top - gap
    )

    # Adding the sale sub-line as a second band row can crowd the reflowed
    # neighbors; if the two-line band introduced an overlap or pushed a box off
    # canvas, drop the candidate rather than emit a colliding receipt.
    if sub_line_added:
        _gate_base_overlap, _gate_base_inversions = _base_layout_counts(
            analysis.receipt
        )
        if not build_layout_integrity_evidence(
            receipt,
            base_overlap_count=_gate_base_overlap,
            base_line_inversion_count=_gate_base_inversions,
        ).get("passed"):
            return None

    # Taxable items reconcile subtotal + TAX (at the merchant's taxable-item rate) +
    # grand total; non-taxable items move subtotal/grand and freeze tax. A
    # taxable item without a stable rate is not reconcilable, so skip it.
    if entry.taxable:
        # Gate on the receipt-validated per-merchant tax config: a taxable edit
        # is only allowed when this merchant is cleared for it, the batch agrees
        # on one validated jurisdiction rate, AND this specific target receipt is
        # confirmed to share that jurisdiction. Apply that validated rate so OCR
        # drift never leaks into recomputed TAX.
        validated_rate = _taxable_edit_rate_for_receipt(
            merchant_name, analyses, analysis
        )
        if validated_rate is None:
            return None
        arithmetic = _apply_taxable_delta(
            receipt, analysis, delta=delta_amount, rate=validated_rate
        )
    else:
        arithmetic = _apply_non_taxable_delta(
            receipt, analysis, delta=delta_amount
        )
    if arithmetic is None:
        return None
    new_total = _parse_money(arithmetic["new_grand_total"]) or _money(
        old_total + delta_amount
    )
    item_count_fields_updated = _reconcile_item_count(receipt, delta_count=1)
    _add_item_base_overlap, _add_item_base_inversions = _base_layout_counts(
        analysis.receipt
    )

    return _candidate_from_receipt(
        receipt,
        merchant_name,
        source="merchant_arithmetic_geometry",
        operation="add_line_item",
        index=index,
        metadata={
            "profile": _profile_summary(profile),
            "base_receipt_key": _receipt_key(analysis.receipt),
            # Score geometry relative to the base so an overlap OR reading-order
            # inversion the reflow introduced between two real rows is caught,
            # not just collisions touching the inserted synthetic row.
            "layout_integrity": build_layout_integrity_evidence(
                receipt,
                base_overlap_count=_add_item_base_overlap,
                base_line_inversion_count=_add_item_base_inversions,
            ),
            "added_item": {
                **entry.to_dict(),
                "line_total": _format_money(delta_amount),
                "row_cloned_from_real_receipt": row_cloned,
                "insertion_position_valid": insertion_position_valid,
                "seen_in_other_receipt": any(
                    key != _receipt_key(analysis.receipt)
                    for key in entry.source_receipt_keys
                ),
            },
            "observed_item_evidence": _observed_item_evidence(
                entry,
                analysis,
                analyses,
            ),
            "category_insertion": {
                "category": entry.category,
                "y_center": round(float(y_center), 1),
                "line_step": line_step,
                "shifted_lower_lines_by": shift_summary["median_shift"],
                "shifted_line_count": shift_summary["line_count"],
                "shifted_lower_line_shift_min": shift_summary["min_shift"],
                "shifted_lower_line_shift_max": shift_summary["max_shift"],
                **insertion_context,
            },
            "old_grand_total": _format_money(old_total),
            "new_grand_total": _format_money(new_total),
            "old_subtotal": (
                _format_money(analysis.subtotal)
                if analysis.subtotal is not None
                else None
            ),
            "new_subtotal": arithmetic["new_subtotal"],
            "tax_delta": arithmetic.get("tax_delta", "0.00"),
            "arithmetic_reconciliation": arithmetic,
            "structure_similarity": _score_structure_similarity(
                _analyze_receipt(receipt),
                analyses,
            ),
            "generation_plan": {
                "operation_plan_rank": plan_rank,
                "operation_plan_count": plan_count,
                "preselection_score": round(float(plan_score), 3),
                "selection_basis": "all_feasible_add_item_plans_ranked_by_candidate_quality",
            },
            "item_count_fields_reconciled": item_count_fields_updated,
            "balancing_strategy": (
                "add observed taxable item; update subtotal, tax (at the "
                "merchant's stable taxable-item rate), and final/payment amounts"
                if entry.taxable
                else "add observed non-taxable item, update subtotal/final/"
                "payment amounts, and leave tax unchanged"
            ),
        },
    )


def _build_add_item_plans(
    merchant_name: str,
    analyses: list[MerchantAnalysis],
    catalog: list[MerchantCatalogEntry],
) -> list[tuple[float, MerchantAnalysis, MerchantCatalogEntry, float]]:
    plans: list[
        tuple[float, MerchantAnalysis, MerchantCatalogEntry, float]
    ] = []
    # Taxable items are only addable when the merchant is receipt-validated for
    # taxable edits AND the TARGET receipt is confirmed to share one validated
    # jurisdiction rate to recompute TAX with; otherwise restrict to non-taxable
    # items. Checked per receipt so a blind off-jurisdiction receipt can't ride a
    # batch that other receipts validated.
    for analysis in analyses:
        if analysis.grand_total is None or not analysis.line_items:
            continue
        receipt_taxable_ok = (
            _taxable_edit_rate_for_receipt(merchant_name, analyses, analysis)
            is not None
        )
        base_key = _receipt_key(analysis.receipt)
        categories = {item.category for item in analysis.line_items}
        existing_products = [item.product_text for item in analysis.line_items]
        for entry in catalog:
            if entry.amount <= Decimal("0.00"):
                continue
            if entry.taxable and not receipt_taxable_ok:
                continue
            if entry.category not in categories:
                continue
            if _has_similar_product(entry.product_text, existing_products):
                continue
            y_center = _category_insert_y(analysis, entry.category)
            if y_center is None:
                continue
            seen_elsewhere = any(
                source_key != base_key
                for source_key in entry.source_receipt_keys
            )
            if not seen_elsewhere:
                continue
            score = entry.count + (10 if seen_elsewhere else 0)
            score += max(0, 6 - len(entry.product_tokens))
            plans.append((score, analysis, entry, y_center))

    return sorted(plans, key=lambda item: item[0], reverse=True)


def _generate_remove_item_candidate(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    *,
    index: int,
    prefer_taxable: bool = False,
) -> dict[str, Any] | None:
    plans = _build_remove_item_plans(merchant_name, analyses)
    if prefer_taxable:
        # Surface a TAXABLE removal specifically (the overall best is usually a
        # lower-amount non-taxable item that would otherwise always win).
        plans = [plan for plan in plans if plan[2].taxable]
    if not plans:
        return None

    candidates = []
    for plan_rank, (plan_score, analysis, removed) in enumerate(
        plans, start=1
    ):
        candidate = _build_remove_item_candidate_from_plan(
            merchant_name,
            profile,
            analyses,
            analysis,
            removed,
            index=index,
            plan_rank=plan_rank,
            plan_count=len(plans),
            plan_score=plan_score,
        )
        if candidate:
            candidates.append(candidate)
    return select_high_fidelity_synthesis_candidate(candidates)


def _build_remove_item_candidate_from_plan(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    analysis: MerchantAnalysis,
    removed: MerchantLineItem,
    *,
    index: int,
    plan_rank: int,
    plan_count: int,
    plan_score: float,
) -> dict[str, Any] | None:
    receipt = copy.deepcopy(analysis.receipt)
    refreshed = _analyze_receipt(receipt)
    if refreshed.grand_total is None:
        return None

    matching = [
        item
        for item in refreshed.line_items
        if _normalize_product_text(item.product_text)
        == _normalize_product_text(removed.product_text)
        and item.amount == removed.amount
    ]
    if not matching:
        return None
    removed = matching[0]
    old_total = refreshed.grand_total
    new_total = _money(max(Decimal("0.00"), old_total - removed.amount))

    removed_center = removed.center_y
    line_step = _line_step(
        refreshed.line_items,
        refreshed.receipt,
        allow_font_geometry_fallback=True,
    )
    # Row-order reflow: delete the item's FULL band by INDEX and pull every lower
    # row up to close the gap — no orphaned satellites, no displaced neighbors.
    shift_summary = _reflow_remove_lines(
        receipt,
        band_indices=removed.band_line_indices or sorted(removed.line_indices),
        line_step=line_step,
    )
    if removed.taxable:
        # Same receipt-validated gate as the add path: only edit taxable items
        # for merchants cleared by the tax config, at the rate confirmed for THIS
        # receipt (rejects mixed/blind-jurisdiction batches).
        validated_rate = _taxable_edit_rate_for_receipt(
            merchant_name, analyses, analysis
        )
        if validated_rate is None:
            return None
        arithmetic = _apply_taxable_delta(
            receipt, refreshed, delta=-removed.amount, rate=validated_rate
        )
    else:
        arithmetic = _apply_non_taxable_delta(
            receipt, refreshed, delta=-removed.amount
        )
    if arithmetic is None:
        return None
    item_count_fields_updated = _reconcile_item_count(receipt, delta_count=-1)
    known_category = removed.category != UNKNOWN_CATEGORY
    category_item_count = (
        sum(1 for item in refreshed.line_items if item.category == removed.category)
        if known_category
        else None
    )
    post_analysis = _analyze_receipt(receipt)
    category_item_count_after = (
        sum(1 for item in post_analysis.line_items if item.category == removed.category)
        if known_category
        else None
    )
    multi_item_category = (
        (category_item_count or 0) > 1
        and (category_item_count_after or 0) >= 1
    )
    category_reason = (
        "removed non-taxable item from a multi-item category"
        if multi_item_category
        else "removed non-taxable item"
    )
    shift_reason = (
        "and shifted lower receipt lines to close the gap"
        if shift_summary["line_count"] > 0
        else "with no lower receipt lines requiring a shift"
    )
    selection_reason = f"{category_reason} {shift_reason}"
    _remove_base_overlap, _remove_base_inversions = _base_layout_counts(
        analysis.receipt
    )

    return _candidate_from_receipt(
        receipt,
        merchant_name,
        source="merchant_arithmetic_geometry",
        operation="remove_line_item",
        index=index,
        metadata={
            "profile": _profile_summary(profile),
            "base_receipt_key": _receipt_key(analysis.receipt),
            # Removal shifts real rows without re-IDing them, so compare overlaps
            # and reading order against the pre-edit receipt to catch any the
            # reflow introduced.
            "layout_integrity": build_layout_integrity_evidence(
                receipt,
                base_overlap_count=_remove_base_overlap,
                base_line_inversion_count=_remove_base_inversions,
            ),
            "removed_item": {
                "product_text": removed.product_text,
                "line_total": _format_money(removed.amount),
                "category": removed.category,
                "taxable": removed.taxable,
            },
            "removal_context": {
                "category": removed.category,
                "removed_y": round(float(removed_center), 1),
                "line_step": line_step,
                "shifted_lower_lines_by": shift_summary["median_shift"],
                "shifted_line_count": shift_summary["line_count"],
                "shifted_lower_line_shift_min": shift_summary["min_shift"],
                "shifted_lower_line_shift_max": shift_summary["max_shift"],
                "category_item_count_before": category_item_count,
                "category_item_count_after": category_item_count_after,
                "selection_reason": selection_reason,
            },
            "old_grand_total": _format_money(old_total),
            "new_grand_total": arithmetic["new_grand_total"],
            "old_subtotal": (
                _format_money(refreshed.subtotal)
                if refreshed.subtotal is not None
                else None
            ),
            "new_subtotal": arithmetic["new_subtotal"],
            "tax_delta": arithmetic.get("tax_delta", "0.00"),
            "arithmetic_reconciliation": arithmetic,
            "structure_similarity": _score_structure_similarity(
                post_analysis,
                analyses,
            ),
            "generation_plan": {
                "operation_plan_rank": plan_rank,
                "operation_plan_count": plan_count,
                "preselection_score": round(float(plan_score), 3),
                "selection_basis": "all_feasible_remove_item_plans_ranked_by_candidate_quality",
            },
            "item_count_fields_reconciled": item_count_fields_updated,
            "balancing_strategy": (
                "remove one taxable item; update subtotal, tax (at the "
                "merchant's stable taxable-item rate), and final/payment amounts"
                if removed.taxable
                else "remove one non-taxable item, update subtotal/final/"
                "payment amounts, and leave tax unchanged"
            ),
        },
    )


def _generate_mutable_field_candidate(
    merchant_name: str,
    profile: dict[str, Any],
    receipts: list[dict[str, Any]],
    analyses: list[MerchantAnalysis],
    *,
    label: str,
    index: int,
) -> dict[str, Any] | None:
    field = (profile.get("mutable_fields") or {}).get(label)
    if not isinstance(field, dict) or field.get("safe_to_mutate") is not True:
        return None

    base_receipts = [
        receipt
        for receipt in receipts
        if any(
            label in word.get("labels", [])
            for word in receipt.get("words", [])
        )
    ]
    if not base_receipts:
        return None

    base = _choose_base_receipt(base_receipts, used=index - 1)
    mutated = copy.deepcopy(base)
    old_text = _replace_first_labeled_word(
        mutated,
        label,
        field=field,
    )
    if old_text is None:
        return None
    new_text = _next_mutable_field_value(label, old_text, field)
    if not new_text or new_text == old_text:
        return None
    _replace_first_labeled_word(mutated, label, field=field, new_text=new_text)
    _refresh_words(mutated)

    return _candidate_from_receipt(
        mutated,
        merchant_name,
        source="merchant_mutable_field_geometry",
        operation="replace_field",
        index=index,
        metadata={
            "profile": _profile_summary(profile),
            "base_receipt_key": _receipt_key(base),
            "field_replacement": {
                "label": label,
                "old_text": old_text,
                "new_text": new_text,
                "format": field.get("stable_format"),
            },
            "mutable_field_evidence": field,
            "structure_similarity": _score_structure_similarity(
                _analyze_receipt(mutated),
                analyses,
            ),
            "balancing_strategy": (
                f"replace {label.lower()} in-place; geometry and labels are preserved"
            ),
        },
    )


def _replace_first_labeled_word(
    receipt: dict[str, Any],
    label: str,
    *,
    field: dict[str, Any],
    new_text: str | None = None,
) -> str | None:
    observed = {str(value) for value in field.get("examples") or []}
    for line in receipt.get("lines", []) or []:
        for word in line.get("words", []) or []:
            if label not in word.get("labels", []):
                continue
            old_text = str(word.get("text") or "")
            if observed and old_text not in observed:
                continue
            if new_text is not None:
                word["text"] = new_text
            return old_text
    return None


def _next_mutable_field_value(
    label: str,
    old_text: str,
    field: dict[str, Any],
) -> str | None:
    pattern = str(field.get("stable_format") or "")
    if label == "DATE":
        return _next_date_value(old_text, pattern)
    if label == "TIME":
        return _next_time_value(old_text, pattern)
    return None


def _next_date_value(old_text: str, pattern: str) -> str | None:
    formats = {
        "MM/DD/YYYY": ("%m/%d/%Y", "%m/%d/%Y"),
        "MM/DD/YY": ("%m/%d/%y", "%m/%d/%y"),
        "YYYY-MM-DD": ("%Y-%m-%d", "%Y-%m-%d"),
        "MM-DD-YYYY": ("%m-%d-%Y", "%m-%d-%Y"),
        "MM-DD-YY": ("%m-%d-%y", "%m-%d-%y"),
    }
    if pattern not in formats:
        return None
    parse_format, output_format = formats[pattern]
    try:
        parsed = datetime.strptime(old_text.strip(), parse_format)
    except ValueError:
        return None
    return (parsed + timedelta(days=1)).strftime(output_format)


def _next_time_value(old_text: str, pattern: str) -> str | None:
    raw = old_text.strip()
    normalized = raw.upper().replace(" ", "")
    formats = {
        "HH:MM": ("%H:%M", "%H:%M"),
        "HH:MM:SS": ("%H:%M:%S", "%H:%M:%S"),
        "HH:MM AM/PM": ("%I:%M%p", "%I:%M%p"),
        "HH:MM:SS AM/PM": ("%I:%M:%S%p", "%I:%M:%S%p"),
    }
    if pattern not in formats:
        return None
    parse_format, output_format = formats[pattern]
    try:
        parsed = datetime.strptime(normalized, parse_format)
    except ValueError:
        return None
    formatted = (parsed + timedelta(minutes=17)).strftime(output_format)
    if "AM/PM" in pattern and " " in raw:
        formatted = formatted[:-2] + " " + formatted[-2:]
    return formatted


# ---------------------------------------------------------------------------
# Value-scrub replace_field: privacy-safe in-place mutation of sensitive
# single-token identifiers (masked PANs, membership / loyalty numbers). Only the
# digit characters are replaced; the mask characters, separators, letters, token
# count, length, label, and bounding box are all preserved. This gives the
# model value variety while scrubbing real card / membership numbers, with no
# geometry risk (the box is untouched) and no arithmetic to reconcile.
# ---------------------------------------------------------------------------

# Labels whose values are safe to scrub digit-by-digit in place.
_SCRUBBABLE_LABELS: tuple[str, ...] = ("PAYMENT_METHOD", "LOYALTY_ID")


def _value_scrub_kind(label: str, text: str) -> str | None:
    """Classify a labeled value as a safe scrub pattern, or None.

    Conservative on purpose: these labels carry a lot of OCR/label noise
    (mislabeled ``O`` words like "gift", "card.", "Pro"), so only values whose
    *whole* token matches a recognizable masked-PAN or numeric-ID shape are
    eligible. Everything else is skipped rather than scrubbed.
    """
    value = text.strip()
    if not value:
        return None
    # Masked PAN: one or more mask chars (X / x / *) then the trailing 4-6 digits
    # a card receipt prints, e.g. "XXXXXXXXXXXX7645", "************5061",
    # "*******2902", "*7645". The mask requirement + >=4 trailing digits keeps
    # this off short codes like "X12". Allowed for card and loyalty labels.
    if re.fullmatch(r"[Xx*]+\d{4,6}", value):
        return "masked_pan"
    # The remaining shapes are membership / loyalty identifiers ONLY. A long
    # digit run or a hyphen-grouped id sitting on PAYMENT_METHOD is far more
    # likely an auth / approval / order code than something to scrub, so we never
    # scrub those — only an explicit LOYALTY_ID label.
    if label != "LOYALTY_ID":
        return None
    if _looks_like_date(value):
        return None
    # Pure numeric membership id, e.g. "112012911712" (>=8 digits to avoid
    # catching short counters / quantities).
    if re.fullmatch(r"\d{8,}", value):
        return "numeric_id"
    # Separator-masked id that prints real mask chars, e.g. "###-###-9416". The
    # mandatory '#' keeps this off ordinary hyphenated numbers and dates.
    if (
        "#" in value
        and re.fullmatch(r"[#\d]+(?:-[#\d]+)+", value)
        and sum(ch.isdigit() for ch in value) >= 3
    ):
        return "separated_id"
    return None


def _looks_like_date(value: str) -> bool:
    """True for common printed date shapes, so scrub never mangles a date that a
    noisy label happened to tag as an identifier."""
    candidate = value.strip()
    return bool(
        re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", candidate)
        or re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", candidate)
        # Compact all-digit date (YYYYMMDD, 1900-2099) that a numeric_id match
        # would otherwise catch and scramble.
        or re.fullmatch(r"(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])", candidate)
    )


def _digit_only_skeleton(text: str) -> str:
    """Replace every digit with '#'. Two values share a skeleton iff they differ
    only in their digits (same length, same mask chars / separators / letters)."""
    return re.sub(r"\d", "#", text)


def _scramble_digits(text: str, seed: str) -> str:
    """Deterministically replace each digit with another digit. Uses a hashlib
    seed (not the salted builtin ``hash``) so the output is stable across runs
    and across processes, which keeps synthesis reproducible."""
    state = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12], 16)
    out: list[str] = []
    for ch in text:
        if ch.isdigit():
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            out.append(str(state % 10))
        else:
            out.append(ch)
    return "".join(out)


def _scrub_value(text: str, seed: str) -> str | None:
    """Return a digit-scrubbed copy that differs from the original, or None if no
    distinct scramble is possible (e.g. a single repeated digit)."""
    scrubbed = _scramble_digits(text, seed)
    if scrubbed != text and _digit_only_skeleton(scrubbed) == _digit_only_skeleton(
        text
    ):
        return scrubbed
    # Re-seed once to avoid the rare identical scramble (e.g. "...0000").
    scrubbed = _scramble_digits(text, seed + "#salt")
    if scrubbed != text and _digit_only_skeleton(scrubbed) == _digit_only_skeleton(
        text
    ):
        return scrubbed
    return None


def _find_scrubbable_word(
    receipt: dict[str, Any],
    label: str,
) -> tuple[dict[str, Any], str, str] | None:
    """First word labeled ``label`` whose text matches a safe scrub pattern."""
    for line in receipt.get("lines", []) or []:
        for word in line.get("words", []) or []:
            if label not in word.get("labels", []):
                continue
            text = str(word.get("text") or "")
            kind = _value_scrub_kind(label, text)
            if kind is not None:
                return word, kind, text
    return None


def _generate_value_scrub_candidate(
    merchant_name: str,
    profile: dict[str, Any],
    receipts: list[dict[str, Any]],
    analyses: list[MerchantAnalysis],
    *,
    label: str,
    index: int,
) -> dict[str, Any] | None:
    field = (profile.get("mutable_fields") or {}).get(label)
    if not isinstance(field, dict) or field.get("safe_to_mutate") is not True:
        return None
    if field.get("mutation_kind") != "value_scrub":
        return None

    base_receipts = [
        receipt
        for receipt in receipts
        if _find_scrubbable_word(receipt, label) is not None
    ]
    if not base_receipts:
        return None

    base = _choose_base_receipt(base_receipts, used=index - 1)
    mutated = copy.deepcopy(base)
    target = _find_scrubbable_word(mutated, label)
    if target is None:
        return None
    word, kind, old_text = target
    seed = f"{_receipt_key(base)}|{label}|{old_text}|{index}"
    new_text = _scrub_value(old_text, seed)
    if not new_text or new_text == old_text:
        return None
    # Scrub EVERY labeled occurrence of this exact value to the same scrubbed
    # value: the same masked card / membership number can be printed more than
    # once, and leaving any copy behind would defeat the privacy scrub and trip
    # the loader's "no residual original" check.
    replaced = 0
    for line in mutated.get("lines", []) or []:
        for candidate_word in line.get("words", []) or []:
            if (
                label in candidate_word.get("labels", [])
                and str(candidate_word.get("text") or "") == old_text
            ):
                candidate_word["text"] = new_text
                replaced += 1
    if replaced == 0:
        return None
    _refresh_words(mutated)

    return _candidate_from_receipt(
        mutated,
        merchant_name,
        source="merchant_value_scrub_geometry",
        operation="replace_field",
        index=index,
        metadata={
            "profile": _profile_summary(profile),
            "base_receipt_key": _receipt_key(base),
            "field_replacement": {
                "label": label,
                "old_text": old_text,
                "new_text": new_text,
                # Matches the contract field's stable_format ("value_scrub"); the
                # specific shape lives in mutable_field_evidence.scrub_kind.
                "format": "value_scrub",
            },
            "mutable_field_evidence": {
                "label": label,
                "safe_to_mutate": True,
                "mutation_kind": "value_scrub",
                "scrub_kind": kind,
                "stable_format": kind,
                # In-place single-word digit scrub: the bounding box is reused
                # verbatim, so geometry is preserved by construction.
                "stable_geometry": True,
                "token_count_preserved": True,
                "format_preserved": True,
                "observed_count": _safe_int(field.get("observed_count")) or 1,
                "examples": field.get("examples", []),
            },
            "structure_similarity": _score_structure_similarity(
                _analyze_receipt(mutated),
                analyses,
            ),
            "balancing_strategy": (
                f"scrub {label.lower()} digits in-place; mask, length, geometry, "
                "token count, and label all preserved"
            ),
        },
    )


# ---------------------------------------------------------------------------
# compose_store_header: swap a receipt's store-identity cluster (address + phone)
# for a DIFFERENT cached branch of the same merchant, sourced coherently from one
# Google Places record. Gives real store-location diversity (a real layout with a
# real sibling store's real address/phone), never mixing fields from two places.
# The address swap is atomic (street + city/state/zip together or not at all) so
# we never emit a half-swapped, incoherent address.
# ---------------------------------------------------------------------------

_STORE_HEADER_LABELS = ("ADDRESS_LINE", "PHONE_NUMBER", "WEBSITE")


def _line_core_labels(line: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for word in line.get("words", []) or []:
        for label in word.get("labels", []) or []:
            upper = str(label).upper()
            if upper in CORE_LABEL_SET:
                labels.add(upper)
    return labels


def _line_owned_by(line: dict[str, Any], label: str) -> bool:
    """True when ``label`` is the line's ONLY core label (header lines that can be
    swapped wholesale without disturbing another entity sharing the line)."""
    return _line_core_labels(line) == {label}


def _swap_owned_line(
    line: dict[str, Any],
    label: str,
    new_text: str,
) -> dict[str, Any] | None:
    """Replace an owned line's words with ``new_text``, reflowed across the line's
    x-span. Returns swap evidence, or None when the value cannot be laid out
    cleanly (caller then skips the swap rather than emit bad geometry)."""
    words = [word for word in line.get("words", []) or [] if isinstance(word, dict)]
    boxes = [
        word["bbox"]
        for word in words
        if isinstance(word.get("bbox"), list) and len(word["bbox"]) == 4
    ]
    tokens = [token for token in str(new_text or "").split() if token]
    if not boxes or not tokens:
        return None
    new_boxes = reflow_line_boxes(boxes, tokens)
    if new_boxes is None:
        return None
    line_id = words[0].get("line_id") if words else line.get("line_id")
    old_text = " ".join(str(word.get("text") or "") for word in words).strip()
    line["words"] = [
        {
            "text": token,
            "bbox": box,
            "labels": [label],
            "line_id": line_id,
            "word_id": position + 1,
        }
        for position, (token, box) in enumerate(zip(tokens, new_boxes))
    ]
    return {
        "label": label,
        "old_text": old_text,
        "new_text": new_text,
        "line_id": line_id,
        "token_count": len(tokens),
    }


def _apply_store_header_swap(
    receipt: dict[str, Any],
    alt: StoreProfile,
) -> list[dict[str, Any]] | None:
    """Swap the receipt's address (atomically) + phone/website for ``alt``'s values.

    Returns the list of field swaps, or None when no coherent swap is possible
    (e.g. the address lines are not cleanly owned by ADDRESS_LINE).
    """
    lines = [line for line in receipt.get("lines", []) or [] if isinstance(line, dict)]
    address_lines = sorted(
        (line for line in lines if _line_owned_by(line, "ADDRESS_LINE")),
        key=lambda line: -_safe_float(line.get("y"), 0.5),
    )
    phone_lines = [line for line in lines if _line_owned_by(line, "PHONE_NUMBER")]
    website_lines = [line for line in lines if _line_owned_by(line, "WEBSITE")]
    if not address_lines or not alt.street or not alt.city_state_zip:
        return None
    if phone_lines and not alt.phone:
        return None
    if website_lines and not alt.website:
        return None

    # The cache gives two address components (street, city/state/zip). A receipt
    # with MORE than two owned address lines would leave a third line carrying
    # the ORIGINAL store's identity next to the new one — skip it rather than
    # ship a mixed address.
    if len(address_lines) > 2:
        return None
    if len(address_lines) == 2:
        address_targets = [
            (address_lines[0], alt.street),
            (address_lines[1], alt.city_state_zip),
        ]
    else:
        address_targets = [
            (address_lines[0], f"{alt.street} {alt.city_state_zip}")
        ]

    swaps: list[dict[str, Any]] = []
    for line, value in address_targets:
        evidence = _swap_owned_line(line, "ADDRESS_LINE", value)
        if evidence is None:
            # Address swap is all-or-nothing: a half-swapped address (new street,
            # old city) is exactly the incoherent receipt we must not produce.
            return None
        swaps.append(evidence)

    # Phone is MANDATORY when the receipt prints one: a new address beside the
    # original branch's phone is a mixed store identity. If any owned phone line
    # cannot be swapped to alt's phone, abandon the whole candidate.
    if phone_lines:
        for line in phone_lines:
            phone_evidence = _swap_owned_line(line, "PHONE_NUMBER", alt.phone)
            if phone_evidence is None:
                return None
            swaps.append(phone_evidence)

    # Website is part of the same printed store-identity cluster. If the base
    # receipt carries one, it must come from the alternate place too; otherwise a
    # new address/phone could sit beside the original branch's website.
    if website_lines:
        for line in website_lines:
            website_evidence = _swap_owned_line(line, "WEBSITE", alt.website)
            if website_evidence is None:
                return None
            swaps.append(website_evidence)

    return swaps or None


def _own_store_profile(receipt: dict[str, Any]) -> StoreProfile | None:
    """The receipt's OWN clean Google Places identity (its real store).

    Prefers the branch-pool entry whose ``place_id`` matches this receipt, then
    its directly-attached ``receipt_place`` record. Returns a complete
    :class:`StoreProfile` (real street + city/state/zip) or ``None`` when the
    receipt has no resolved place to clean toward. Never returns a *different*
    branch: cleaning must preserve this store's real identity, not diversify it
    (that is ``compose_store_header``'s job).
    """
    own_place_id = str(receipt.get("place_id") or "").strip()
    receipt_place = receipt.get("receipt_place")
    records: list[dict[str, Any]] = []
    if isinstance(receipt_place, dict):
        records.append(receipt_place)
    pool = receipt.get("merchant_place_pool")
    if isinstance(pool, list):
        records.extend(row for row in pool if isinstance(row, dict))
    if not records:
        return None

    if own_place_id:
        for profile in extract_store_profiles(records):
            if profile.place_id == own_place_id and profile.is_complete():
                return profile
    # No usable place_id match: fall back to the receipt's OWN attached record
    # only (never a sibling from the pool).
    if isinstance(receipt_place, dict):
        for profile in extract_store_profiles([receipt_place]):
            if profile.is_complete():
                return profile
    return None


def _store_identity_key(text: Any) -> str:
    """Loose comparison key for a printed store-identity value (case/punctuation/
    whitespace insensitive) so an already-clean header is recognized as a no-op."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _printed_store_identity(receipt: dict[str, Any]) -> tuple[str, str, str]:
    """The receipt's currently-printed (address, phone, website) text drawn from
    the owned store-identity lines, joined top-to-bottom."""
    lines = [
        line for line in receipt.get("lines", []) or [] if isinstance(line, dict)
    ]

    def _text(line: dict[str, Any]) -> str:
        return " ".join(
            str(word.get("text") or "")
            for word in line.get("words", []) or []
        ).strip()

    address_lines = sorted(
        (line for line in lines if _line_owned_by(line, "ADDRESS_LINE")),
        key=lambda line: -_safe_float(line.get("y"), 0.5),
    )
    address = " ".join(_text(line) for line in address_lines).strip()
    phone = " ".join(
        _text(line) for line in lines if _line_owned_by(line, "PHONE_NUMBER")
    ).strip()
    website = " ".join(
        _text(line) for line in lines if _line_owned_by(line, "WEBSITE")
    ).strip()
    return address, phone, website


def normalize_store_identity(
    receipt: dict[str, Any],
    *,
    operation: str,
) -> dict[str, Any]:
    """Universal store-identity CLEANING applied to EVERY synthesized candidate.

    Real receipt OCR carries garbled store-identity tokens (a split street like
    ``Russel| Rd``, a truncated ``CA. 91`` ZIP, a stray ``#673 673`` tail, an
    email mislabeled MERCHANT_NAME). This swaps the (possibly garbled) printed
    store cluster for the receipt's OWN real Google Places values, reusing the
    atomic :func:`_apply_store_header_swap` machinery so the same guards apply
    (>2 owned address lines, mandatory matching phone/website, all-or-nothing
    reflow) and an un-cleanable header is left untouched rather than half-swapped.

    This is a CLEANING pass, not a diversity swap: it never substitutes a
    *different* branch. ``compose_store_header`` candidates were deliberately
    given a clean *sibling* branch for location diversity, so they are skipped
    here (re-cleaning would fight that intentional swap).

    Mutates ``receipt`` in place when a clean is applied. Returns metadata that
    always carries ``applied``; a ``reason`` is set whenever it is skipped.
    """
    if operation == "compose_store_header":
        return {
            "applied": False,
            "reason": "compose_store_header_clean_sibling",
        }

    profile = _own_store_profile(receipt)
    if profile is None:
        return {"applied": False, "reason": "no_resolved_place"}

    address, phone, website = _printed_store_identity(receipt)
    if not address and not phone and not website:
        return {"applied": False, "reason": "no_store_identity_printed"}

    # Already-clean no-op: the printed address already equals the real place's
    # (case/punctuation/spacing aside), and any printed phone/website matches too,
    # so there is nothing garbled to fix — avoid a needless geometry reflow.
    clean_address = f"{profile.street} {profile.city_state_zip}"
    already_clean = bool(address) and (
        _store_identity_key(address) == _store_identity_key(clean_address)
    )
    if already_clean and phone and profile.phone:
        already_clean = _store_identity_key(phone) == _store_identity_key(
            profile.phone
        )
    if already_clean and website and profile.website:
        already_clean = _store_identity_key(website) == _store_identity_key(
            profile.website
        )
    if already_clean:
        return {
            "applied": False,
            "reason": "already_clean",
            "place_id": profile.place_id,
        }

    # The atomic swap requires a phone/website when the receipt PRINTS one (so a
    # cleaned address never sits beside a different store's phone). When the
    # resolved place did not capture this store's phone/website, the printed value
    # IS this same store's own — backfill it so the address still gets cleaned and
    # the store's own phone/website is preserved verbatim (no cross-store mixing,
    # since street + city/state/zip and any place-sourced field still come from
    # this one place). When the place HAS the field, its clean value wins.
    swap_profile = profile
    if (phone and not profile.phone) or (website and not profile.website):
        swap_profile = replace(
            profile,
            phone=profile.phone or (phone or None),
            website=profile.website or (website or None),
        )
    swaps = _apply_store_header_swap(receipt, swap_profile)
    if not swaps:
        # The atomic guards rejected the swap (no owned address anchor, a printed
        # phone/website the place lacks, >2 address lines, or un-reflowable
        # geometry). Leave the header as-is rather than emit an incoherent one.
        return {
            "applied": False,
            "reason": "unswappable_guarded",
            "place_id": profile.place_id,
        }
    _refresh_words(receipt)
    return {
        "applied": True,
        "place_id": profile.place_id,
        "source_merchant_name": profile.merchant_name,
        "cleaned_from_own_place": True,
        "all_fields_from_single_place": True,
        "phone_from_place": bool(profile.phone),
        "website_from_place": bool(profile.website),
        "phone_preserved_from_receipt": bool(phone and not profile.phone),
        "website_preserved_from_receipt": bool(website and not profile.website),
        "swapped_labels": sorted({swap["label"] for swap in swaps}),
        "fields_swapped": swaps,
    }


_GENERIC_STORE_NAME_TOKENS = {"the", "a", "an"}


def _store_merchant_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9']+", str(name).lower())
    while tokens and tokens[0] in _GENERIC_STORE_NAME_TOKENS:
        tokens.pop(0)
    return tokens


def _store_merchant_match(name_a: str, name_b: str) -> bool:
    """Same-brand check for Places pools without collapsing unrelated merchants.

    A bare brand can match a location-qualified name (``Gelson's`` vs
    ``Gelson's Westlake Village``), and a leading article is ignored
    (``The Home Depot`` vs ``Home Depot``). Names that only share an umbrella
    first token but diverge immediately (``Amazon Fresh`` vs ``Amazon Go``) do
    not match.
    """

    a, b = _store_merchant_tokens(name_a), _store_merchant_tokens(name_b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) < len(long) and long[: len(short)] == short


def _generate_compose_store_header_candidates(
    merchant_name: str,
    profile: dict[str, Any],
    receipts: list[dict[str, Any]],
    analyses: list[MerchantAnalysis],
    *,
    start_index: int,
    limit: int,
) -> list[dict[str, Any]]:
    # Prefer the merchant's full branch pool (includes Places-fetched siblings
    # not tied to any receipt); fall back to the receipts' own attached places.
    place_records: list[dict[str, Any]] = []
    for receipt in receipts:
        pool_records = receipt.get("merchant_place_pool")
        if isinstance(pool_records, list) and pool_records:
            place_records = [r for r in pool_records if isinstance(r, dict)]
            break
    if not place_records:
        place_records = [
            receipt.get("receipt_place")
            for receipt in receipts
            if isinstance(receipt.get("receipt_place"), dict)
        ]
    # Scope to THIS merchant: the payload-wide pool can include other merchants'
    # places (mixed-merchant exports), and an unrelated branch must never become
    # a header source.
    place_records = [
        record
        for record in place_records
        if isinstance(record, dict)
        and _store_merchant_match(
            merchant_name, str(record.get("merchant_name") or "")
        )
    ]
    pool = extract_store_profiles(place_records)
    if len([profile for profile in pool if profile.is_complete()]) < 2:
        return []  # no alternate branch -> no coherent location diversity

    bases = [receipt for receipt in receipts if receipt.get("place_id")]
    if not bases:
        return []
    # Rotate the base order so repeated generations vary which receipts are used.
    offset = start_index % len(bases)
    ordered_bases = bases[offset:] + bases[:offset]

    candidates: list[dict[str, Any]] = []
    index = start_index
    for base in ordered_bases:
        if len(candidates) >= limit:
            break
        own_place_id = str(base.get("place_id") or "")
        alts = alternate_profiles(pool, own_place_id)
        if not alts:
            continue
        alt = alts[index % len(alts)]
        if not _store_merchant_match(merchant_name, alt.merchant_name):
            continue
        mutated = copy.deepcopy(base)
        swaps = _apply_store_header_swap(mutated, alt)
        if not swaps:
            continue
        _refresh_words(mutated)
        candidates.append(
            _candidate_from_receipt(
                mutated,
                merchant_name,
                source="merchant_store_header_geometry",
                operation="compose_store_header",
                index=index,
                metadata={
                    "profile": _profile_summary(profile),
                    "base_receipt_key": _receipt_key(base),
                    "store_header_swap": {
                        "own_place_id": own_place_id,
                        "source_place_id": alt.place_id,
                        "source_merchant_name": alt.merchant_name,
                        "merchant_match": True,
                        "all_fields_from_single_place": True,
                        "swapped_labels": sorted(
                            {swap["label"] for swap in swaps}
                        ),
                        "fields_swapped": swaps,
                    },
                    "structure_similarity": _score_structure_similarity(
                        _analyze_receipt(mutated),
                        analyses,
                    ),
                    "balancing_strategy": (
                        "swap the store-identity cluster (address + phone) for a "
                        "different cached branch of the same merchant; geometry "
                        "reflowed in-band, labels preserved, single source place"
                    ),
                },
            )
        )
        index += 1
    return candidates


def _build_remove_item_plans(
    merchant_name: str,
    analyses: list[MerchantAnalysis],
) -> list[tuple[float, MerchantAnalysis, MerchantLineItem]]:
    plans: list[tuple[float, MerchantAnalysis, MerchantLineItem]] = []
    # Taxable items are only removable when the merchant is receipt-validated for
    # taxable edits AND the TARGET receipt is confirmed to share one validated
    # jurisdiction rate to recompute TAX with; otherwise restrict to non-taxable
    # items. Checked per receipt (see _build_add_item_plans).
    for analysis in analyses:
        if analysis.grand_total is None or len(analysis.line_items) < 2:
            continue
        receipt_taxable_ok = (
            _taxable_edit_rate_for_receipt(merchant_name, analyses, analysis)
            is not None
        )
        category_counts = Counter(
            item.category for item in analysis.line_items
        )
        for item in analysis.line_items:
            if item.amount <= Decimal("0.00"):
                continue
            if item.taxable and not receipt_taxable_ok:
                continue
            if (
                item.category != UNKNOWN_CATEGORY
                and category_counts[item.category] <= 1
            ):
                continue
            score = max(0, 20 - float(item.amount))
            score += 8 if item.category != UNKNOWN_CATEGORY else 0
            score += min(6, category_counts[item.category])
            plans.append((score, analysis, item))
    return sorted(plans, key=lambda item: item[0], reverse=True)


# ---------------------------------------------------------------------------
# Online-catalog template fill (compose_online_catalog)
#
# Render fresh online products into a merchant's real row FORMAT with labels we
# assign, then COMPOSE them onto the geometrically cleanest real scaffold. The
# item region carries clean supervision (UPC/flag -> O, name -> PRODUCT_NAME,
# price -> LINE_TOTAL) because we control it; the header/totals/footer come from
# a real receipt so the surrounding structure stays authentic.
# ---------------------------------------------------------------------------

_ONLINE_NAME_STOPWORDS = {"THE", "PLUS", "PREMIUM", "OF", "AND", "WITH", "FOR"}


def _abbrev_product_name(name: str, *, max_len: int = 24) -> str:
    """Receipt-style abbreviation: uppercase, drop filler, clip to a width that
    fits the name column (real receipts abbreviate item names too).

    Clipping is at WORD boundaries (never mid-token, which would leave a partial
    number like ``12CT`` -> ``1``), and BARE-NUMBER tokens are dropped (a
    standalone digit like the ``1`` in ``1 PINT`` reads as a quantity/UPC). Both
    keep every rendered name token cleanly ``PRODUCT_NAME`` so the composed-row
    label-control check passes.
    """
    tokens = [
        token
        for token in str(name).upper().split()
        if token not in _ONLINE_NAME_STOPWORDS
    ]
    kept: list[str] = []
    length = 0
    for token in tokens:
        extra = len(token) + (1 if kept else 0)
        if length + extra > max_len:
            break
        kept.append(token)
        length += extra
    kept = [token for token in kept if not token.isdigit()]
    if not kept:
        # Fall back to the first NON-numeric token only — never a bare digit,
        # which would render as PRODUCT_NAME but read as O and fail label
        # control. An all-numeric name yields "" (the caller skips such entries).
        kept = [token for token in tokens if not token.isdigit()][:1]
    return " ".join(kept)


def _template_fill_geometry(analysis: MerchantAnalysis) -> dict[str, Any]:
    """Char pitch, name/price columns and row height from the scaffold's ITEM
    region (the small print), not the larger header fonts. The local
    ``width / len(text)`` pitch is the same measurement PR #994's
    ``width_per_char`` formalizes.

    The scaffold's OWN item-region geometry is always preferred (it is the most
    specific signal for the receipt being edited). PR #994's merchant font
    profile — supplied via ``receipt["font_geometry"]`` (see
    ``generate_merchant_synthesis_candidates``) — is used only as a FALLBACK for
    rows the scaffold cannot measure, replacing flat constants with the
    merchant's measured char width / glyph height / price column. This sharpens
    sparse scaffolds without ever overriding real geometry, so the structure gate
    is unaffected when the scaffold has its own measurements.
    """
    receipt = analysis.receipt
    fg = receipt.get("font_geometry") or {}
    char_w_fallback = _font_geometry_px(fg, "char_width_px", default=16)
    height_fallback = _font_geometry_px(fg, "font_height_px", default=18)
    band_idx = {
        index
        for item in analysis.line_items
        for index in item.band_line_indices
    }
    char_ws: list[float] = []
    heights: list[float] = []
    right_margin = 0
    for index, line in enumerate(receipt.get("lines", []) or []):
        for word in line.get("words", []) or []:
            bbox = word.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                continue
            right_margin = max(right_margin, bbox[2])
            text = str(word.get("text") or "")
            if index in band_idx and len(text) >= 3:
                char_ws.append((bbox[2] - bbox[0]) / len(text))
                heights.append(bbox[3] - bbox[1])
    char_w = max(
        6, int(statistics.median(char_ws)) if char_ws else char_w_fallback
    )
    # Right-anchor composed prices so their RIGHT EDGE lands on the scaffold's
    # real LINE_TOTAL right-edge column (every item price ends at the same x —
    # the #1 realism tell). Fall back to the merchant profile's price column
    # (center + half a "$dd.dd" width), then to the receipt's right margin, when
    # this scaffold has no measurable LINE_TOTAL right edge.
    typical_price_half_width = 3 * char_w  # half of "$dd.dd"
    # _label_right_x_p50 returns None when the scaffold has no measurable
    # LINE_TOTAL right edge, so treat that (or a non-positive value) as
    # "unmeasured" and fall back to the merchant profile, then the right margin.
    real_price_right = _label_right_x_p50(receipt, "LINE_TOTAL")
    profile_price_center = _font_geometry_px(fg, "price_column_x_px", default=0)
    if real_price_right and real_price_right > 0:
        price_x1 = int(real_price_right)
    elif profile_price_center:
        price_x1 = int(profile_price_center + typical_price_half_width)
    else:
        price_x1 = int(right_margin) or 960
    price_x1 = min(price_x1, 996)
    return {
        "char_w": char_w,
        # name column starts just past a 12-digit UPC + one space
        "name_x0": 8 + 13 * char_w,
        "price_x1": price_x1,
        "height": int(statistics.median(heights)) if heights else height_fallback,
    }


def _font_geometry_px(
    font_geometry: dict[str, Any], key: str, *, default: int
) -> int:
    """A positive integer pixel value from a font-geometry dict, else ``default``.

    ``font_geometry`` mirrors ``MerchantFontProfile.to_geometry_params()`` where
    a field can be ``None`` (the profile could not observe it). Such fields fall
    through to ``default`` so the synthesizer never trusts a missing measurement.
    """
    value = font_geometry.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number <= 0 or number != number:  # reject non-positive / NaN
        return default
    return int(round(number))


def _build_template_filled_row(
    entry: OnlineCatalogEntry,
    *,
    y0: int,
    geo: dict[str, Any],
    line_id: int,
    merchant_name: str | None = None,
    template=None,
) -> dict[str, Any] | None:
    """One item row with NON-OVERLAPPING columns and labels we assign:
    UPC/flag/marker -> O, name tokens -> PRODUCT_NAME, price -> LINE_TOTAL.

    For a KNOWN merchant (one with a validated tax profile) the flag column
    carries the merchant's real tax-class token (e.g. Vons ``T``/``S``); for an
    unknown merchant (or a known merchant with no flag for this item's class) it
    keeps the conservative neutral ``<A>`` placeholder. Either way the flag slot
    is always rendered and always labeled ``O``.

    When the merchant's item-line grammar prints a markdown marker (e.g. Amazon's
    ``NOW``), it is rendered in its own slot immediately left of the price, also
    labeled ``O``. Token widths stay at the load-bearing ``char_w``; only the
    inter-word/column GAPS use the tight ``_template_word_gap`` so the row reads
    as a single word-space instead of a wide justified sprawl."""
    char_w = geo["char_w"]
    gap = _template_word_gap(geo["height"])
    y1 = y0 + geo["height"]
    words: list[dict[str, Any]] = []

    price_text = f"${_format_money(entry.price)}"
    price_x1 = geo["price_x1"]
    price_x0 = price_x1 - len(price_text) * char_w
    marker_token = (
        template.markdown_marker.token
        if (template is not None and template.markdown_marker.present)
        else None
    )
    flag_token = _merchant_item_tax_flag(merchant_name, entry.taxable) or "<A>"
    if marker_token:
        # Marked-down row: render "<name> <marker> <price> <flag>" -- the sale
        # marker ("now") sits immediately LEFT of the price and the tax flag
        # immediately RIGHT of it (trailing), mirroring a real Amazon
        # "now $4.99 F" row. (The prior layout emitted flag -> marker -> price.)
        marker_w = max(1, len(marker_token)) * char_w
        marker_x1 = price_x0 - gap
        marker_x0 = marker_x1 - marker_w
        flag_w = max(1, len(flag_token)) * char_w
        # Trailing flag, right of the price; clamp to the canvas without ever
        # inverting the box (keep a positive width even if the price column hugs
        # the right edge).
        flag_x1 = min(1000, price_x1 + gap + flag_w)
        flag_x0 = flag_x1 - flag_w
        name_limit = marker_x0 - gap
    else:
        # Plain row: the flag keeps its conventional slot just LEFT of the price.
        marker_x0 = marker_x1 = None
        flag_x1 = price_x0 - gap
        flag_x0 = flag_x1 - 3 * char_w
        name_limit = flag_x0 - gap

    word_id = 1
    upc = str(entry.upc or "")
    if upc:
        words.append(
            {
                "text": upc,
                "bbox": [8, y0, 8 + len(upc) * char_w, y1],
                "labels": [],
                "line_id": line_id,
                "word_id": word_id,
            }
        )
        word_id += 1
    cursor = geo["name_x0"]
    product_name_words = 0
    for token in _abbrev_product_name(entry.name).split():
        width = len(token) * char_w
        if cursor + width > name_limit:  # keep clear of the flag/marker/price
            if product_name_words:
                break
            max_chars = max(0, (name_limit - cursor) // char_w)
            if max_chars <= 0:
                return None
            token = token[:max_chars]
            width = len(token) * char_w
            if not token or cursor + width > name_limit:
                return None
        words.append(
            {
                "text": token,
                "bbox": [cursor, y0, cursor + width, y1],
                "labels": ["PRODUCT_NAME"],
                "line_id": line_id,
                "word_id": word_id,
            }
        )
        cursor += width + gap
        word_id += 1
        product_name_words += 1
    if product_name_words <= 0:
        return None
    flag_word = {
        "text": flag_token,
        "bbox": [flag_x0, y0, flag_x1, y1],
        "labels": [],
        "line_id": line_id,
        "word_id": word_id,
    }
    price_word = {
        "text": price_text,
        "bbox": [price_x0, y0, price_x1, y1],
        "labels": ["LINE_TOTAL"],
        "line_id": line_id,
        "word_id": word_id + 1,
    }
    if marker_token:
        # Order: marker -> price -> flag (marker left of price, flag trailing).
        marker_word = {
            "text": marker_token,
            "bbox": [marker_x0, y0, marker_x1, y1],
            "labels": [],
            "line_id": line_id,
            "word_id": word_id,
        }
        price_word["word_id"] = word_id + 1
        flag_word["word_id"] = word_id + 2
        words.extend([marker_word, price_word, flag_word])
    else:
        # Order: flag -> price (flag in its conventional pre-price slot).
        words.extend([flag_word, price_word])
    return {"line_id": line_id, "y": y0 / 1000, "words": words}


def _expected_template_label(
    text: Any,
    *,
    word_index: int | None = None,
    flag_index: int | None = None,
) -> str:
    """The label a composed item-row token MUST carry (the supervision we assert
    and later verify, so a regression in row rendering is caught)."""
    value = str(text or "")
    if value.startswith("$") and _parse_money(value) is not None:
        return "LINE_TOTAL"
    if value == "<A>":
        return "O"
    if (
        word_index is not None
        and flag_index is not None
        and word_index == flag_index
    ):
        # The merchant tax-class flag slot (a real flag token like "T"/"F"/"A"
        # for a known merchant, or the neutral "<A>" for an unknown one). Always
        # supervision-neutral, exactly as "<A>" has always been.
        return "O"
    if (
        word_index == 0
        and value.isdigit()
        and len(value) >= 8
    ):
        return "O"
    if (
        word_index is not None
        and flag_index is not None
        and word_index < flag_index
    ):
        return "PRODUCT_NAME"
    if value.isdigit():
        return "O"
    return "PRODUCT_NAME"


def _verify_template_row_labels(receipt: dict[str, Any]) -> dict[str, Any]:
    """Confirm every composed item-row token (line_id >= base) carries exactly
    the label we assigned — the clean-supervision guarantee of this path."""
    total = 0
    correct = 0
    row_count = 0
    rows_with_product_name = 0
    product_name_token_count = 0
    for line in receipt.get("lines", []) or []:
        if (line.get("line_id") or 0) < _SYNTHETIC_LINE_ID_BASE:
            continue
        words = line.get("words", []) or []
        # A composed sale sub-line (``SALE 1 $x, WAS: $y each``) carries NO labels
        # on any token — it is decorative real-format copy, expected ALL-``O``, not
        # item supervision. Verify that and skip the primary-row product/price
        # accounting so it never demands a PRODUCT_NAME / LINE_TOTAL.
        if words and all(not (word.get("labels") or []) for word in words):
            for word in words:
                labels = word.get("labels") or []
                got = labels[0] if labels else "O"
                total += 1
                correct += int(got == "O")
            continue
        row_count += 1
        # Locate the flag column positionally — that covers both the neutral
        # "<A>" placeholder and a known merchant's real flag token. On a plain row
        # the flag sits immediately BEFORE the price (LINE_TOTAL). On a marked-down
        # row a markdown marker ("now") sits immediately LEFT of the price and the
        # flag is TRAILING (immediately right of the price): "<name> now $X.XX F".
        # The marker and flag both remain supervision-neutral (O). Fall back to the
        # "<A>" text match if no price word is present.
        price_index = next(
            (
                index
                for index, word in enumerate(words)
                if str(word.get("text") or "").startswith("$")
                and _parse_money(word.get("text")) is not None
            ),
            None,
        )
        marker_index = None
        if (
            price_index is not None
            and price_index > 0
            and _MARKDOWN_MARKER_RE.match(
                str(words[price_index - 1].get("text") or "")
            )
        ):
            marker_index = price_index - 1
        if marker_index is not None:
            flag_index = (
                price_index + 1 if price_index + 1 < len(words) else None
            )
        elif price_index is not None and price_index > 0:
            flag_index = price_index - 1
        else:
            flag_index = next(
                (
                    index
                    for index, word in enumerate(words)
                    if str(word.get("text") or "") == "<A>"
                ),
                None,
            )
        row_has_product_name = False
        for index, word in enumerate(words):
            if index == marker_index:
                expected = "O"  # the markdown sale marker slot
            else:
                expected = _expected_template_label(
                    word.get("text"),
                    word_index=index,
                    flag_index=flag_index,
                )
            labels = word.get("labels") or []
            got = labels[0] if labels else "O"
            total += 1
            correct += int(got == expected)
            if expected == "PRODUCT_NAME":
                row_has_product_name = True
                product_name_token_count += 1
        rows_with_product_name += int(row_has_product_name)
    return {
        "item_token_count": total,
        "correctly_labeled": correct,
        "product_name_token_count": product_name_token_count,
        "all_rows_have_product_name": row_count > 0
        and rows_with_product_name == row_count,
        "all_correct": total > 0
        and correct == total
        and row_count > 0
        and rows_with_product_name == row_count,
    }


# Plausible US sales-tax band; ratios outside it are OCR/label noise (e.g. a
# mis-read tax token of $58 on an $18 subtotal), not a tax rate.
_MIN_PLAUSIBLE_TAX_RATE = Decimal("0.001")
_MAX_PLAUSIBLE_TAX_RATE = Decimal("0.20")


def _stable_tax_rate(
    analyses: list[MerchantAnalysis],
) -> tuple[Decimal | None, bool, list[Decimal]]:
    """Robust EFFECTIVE tax rate (median of ``tax_total / subtotal``) plus
    whether it is stable across the merchant's receipts.

    Per-item taxability detection is unreliable (many receipts parse 0-1 items),
    so ``tax / taxable_subtotal`` is fragile. The labeled SUBTOTAL and TAX
    summary amounts are reliable, and their ratio is the effective rate that
    reproduces the merchant's real tax-to-subtotal relationship on a composed
    receipt. Implausible ratios are dropped before the median. The rate is
    EVIDENCE for composing an internally consistent net-new receipt — not
    permission to edit a real receipt's tax, which the edit path still freezes.
    """
    rates: list[Decimal] = []
    for analysis in analyses:
        subtotal = analysis.subtotal
        tax_total = analysis.tax_total
        if (
            subtotal is None
            or tax_total is None
            or subtotal <= Decimal("0.00")
            or tax_total <= Decimal("0.00")
        ):
            continue
        rate = (tax_total / subtotal).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if _MIN_PLAUSIBLE_TAX_RATE <= rate <= _MAX_PLAUSIBLE_TAX_RATE:
            rates.append(rate)
    if len(rates) < 2:
        return None, False, rates
    median = statistics.median(rates)
    if not isinstance(median, Decimal):
        median = Decimal(str(median))
    median = median.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    # Stable when a clear majority cluster within a percentage point of the
    # median (tolerates a minority of different-jurisdiction receipts).
    within = sum(
        1 for rate in rates if abs(rate - median) <= Decimal("0.01")
    )
    stable = (within / len(rates)) >= 0.6
    return median, stable, rates


def _rotate(values: list[Any], offset: int) -> list[Any]:
    if not values:
        return []
    pivot = offset % len(values)
    return values[pivot:] + values[:pivot]


# Two lines whose word centers fall within this many y-px are one visual row (the
# same tolerance verify_candidates.py / the renderer use to group words into
# lines), so a labeled real row whose label and right-aligned amount live on
# separate line objects is re-pitched as a single unit, never split apart.
_VISUAL_ROW_Y_TOL = 8.0


def _space_line_block_to_pitch(
    lines: list[dict[str, Any]],
    *,
    top_y: float,
    pitch: int,
) -> float:
    """Stack the VISUAL ROWS of ``lines`` top-to-bottom at a uniform ``pitch``.

    Coordinates are the synthesis ``y-high-is-top`` system, so successive rows
    step DOWN by ``pitch`` (decreasing y). Lines whose word centers fall within
    ``_VISUAL_ROW_Y_TOL`` are first clustered into one visual row (a label and its
    right-aligned amount commonly live on separate line objects at the same
    baseline), then each whole row is translated as a single unit — every word in
    every line of the row shifts by the SAME vertical delta — so a labeled real
    row's internal word geometry, labels, reading order and label/amount alignment
    are preserved while only the row-to-row pitch is normalized. It never
    re-spaces words inside a row or nudges one line on its own.

    The first row's top edge is placed at ``top_y`` and every later row's top sits
    ``pitch`` below the previous one. Returns the top-y one ``pitch`` below the
    last placed row, so the caller can chain another block (e.g. the summary
    directly beneath the item rows).
    """
    measured: list[tuple[float, float, dict[str, Any]]] = []
    for line in lines:
        boxes = [
            word["bbox"]
            for word in line.get("words", []) or []
            if word.get("bbox")
        ]
        if not boxes:
            continue
        center = sum((box[1] + box[3]) / 2.0 for box in boxes) / len(boxes)
        top = max(box[3] for box in boxes)
        measured.append((center, top, line))
    if not measured:
        return float(top_y)

    # Cluster into visual rows top-to-bottom (highest center first).
    measured.sort(key=lambda item: item[0], reverse=True)
    rows: list[list[tuple[float, float, dict[str, Any]]]] = []
    for entry in measured:
        if rows and abs(entry[0] - rows[-1][-1][0]) <= _VISUAL_ROW_Y_TOL:
            rows[-1].append(entry)
        else:
            rows.append([entry])

    target_top = float(top_y)
    for row in rows:
        row_top = max(top for _, top, _ in row)
        delta = int(round(target_top - row_top))
        for _, _, line in row:
            for word in line.get("words", []) or []:
                box = word.get("bbox")
                if box:
                    box[1] += delta
                    box[3] += delta
            line["y"] = _line_y(line)
        target_top -= pitch
    return target_top


def _compose_online_catalog_receipt(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    scaffold: MerchantAnalysis,
    entries: list[OnlineCatalogEntry],
    rate: Decimal | None,
    rate_stable: bool,
    *,
    index: int,
) -> dict[str, Any] | None:
    """Compose one net-new receipt: real scaffold header/totals/footer + freshly
    rendered online item rows, with recomputed subtotal / tax / grand total."""
    items = scaffold.line_items
    band_idx = sorted(
        {i for item in items for i in item.band_line_indices}
    )
    if not band_idx:
        return None
    receipt = copy.deepcopy(scaffold.receipt)
    lines = receipt["lines"]
    first_i, last_i = min(band_idx), max(band_idx)
    header, summary = lines[:first_i], lines[last_i + 1 :]
    geo = _template_fill_geometry(scaffold)
    height = geo["height"]
    # The merchant's item-line grammar (markdown marker + sale sub-line), or None
    # when the merchant has no research export; rendered into each composed row.
    template = _item_line_template_for_merchant(
        _merchant_research_slug(merchant_name)
    )
    sub_line_present = template is not None and template.sub_line.present

    # The merchant's single measured item-row pitch (font-geometry fallback when a
    # sparse scaffold has no measurable row geometry). It is the row rhythm for
    # BOTH the generated item rows and the summary/payment block below them, so the
    # composed receipt never shows a jammed-pitch totals block overlapping itself.
    pitch = _line_step(
        items, scaffold.receipt, allow_font_geometry_fallback=True
    )

    # Build the generated item rows; their final y is assigned by the pitch pass
    # below, so the provisional top only has to be on-canvas and ordered. When the
    # merchant prints a sale sub-line, append it directly after its item row as a
    # second all-O line so _space_line_block_to_pitch lays both out in sequence.
    composed: list[dict[str, Any]] = []
    # Provisional rows are stepped well clear of the visual-row tolerance so each
    # generated item stays its own row when the pitch pass below clusters lines.
    provisional_step = max(int(pitch), int(_VISUAL_ROW_Y_TOL) * 2 + height)
    provisional_top = 800
    line_id = _SYNTHETIC_LINE_ID_BASE
    for entry in entries:
        row = _build_template_filled_row(
            entry,
            y0=max(12, provisional_top - height),
            geo=geo,
            line_id=line_id,
            merchant_name=merchant_name,
            template=template,
        )
        if row is None:
            return None
        composed.append(row)
        line_id += 1
        provisional_top -= provisional_step
        if sub_line_present:
            sub = _build_sale_sub_line(
                template=template,
                price=entry.price,
                line_id=line_id,
                y0=max(12, provisional_top - height),
                char_w=geo["char_w"],
                height=height,
                x0=geo["name_x0"],
            )
            if sub is not None:
                composed.append(sub)
                line_id += 1
                provisional_top -= provisional_step

    # Lay the generated item rows out at the merchant pitch starting one pitch-gap
    # below the header's lowest word, then chain the real summary/footer block
    # directly beneath at the same pitch. _space_line_block_to_pitch translates
    # whole lines only, so the real totals/payment rows keep their internal label
    # geometry while the block gains a consistent, non-overlapping vertical rhythm.
    header_bottom = min(
        (
            word["bbox"][1]
            for line in header
            for word in line.get("words", []) or []
            if word.get("bbox")
        ),
        default=900,
    )
    first_item_top = header_bottom - max(6, pitch - height)
    next_top = _space_line_block_to_pitch(
        composed, top_y=first_item_top, pitch=pitch
    )
    _space_line_block_to_pitch(summary, top_y=next_top, pitch=pitch)

    receipt["lines"] = header + composed + summary

    subtotal = _money_sum(entry.price for entry in entries)
    taxable_subtotal = _money_sum(
        entry.price for entry in entries if entry.taxable
    )
    # Only rendered entries marked taxable contribute to TAX. Exempt catalog rows
    # (common for grocery compositions) must not inherit positive tax just because
    # the merchant has a stable observed rate.
    if rate is not None and taxable_subtotal > Decimal("0.00"):
        tax = _money(taxable_subtotal * rate)
    else:
        tax = Decimal("0.00")
    total = _money(subtotal + tax)
    updated_labels = _write_composed_totals(
        receipt,
        subtotal,
        tax,
        total,
        old_grand_total=scaffold.grand_total,
    )
    # The composed item count differs from the scaffold's, so reconcile any
    # "ITEMS SOLD" / item-count summary the footer carries.
    item_count_fields_updated = _reconcile_item_count(
        receipt, delta_count=len(entries) - len(items)
    )

    _fit_receipt_to_canvas(receipt)
    _refresh_words(receipt)

    # Gate on geometry the composition itself produced: if a generated row collides
    # with a neighbor or the totals/payment block (or any box is invalid/off
    # canvas), drop this candidate so generation retries another scaffold / item
    # count rather than emitting an overlapping receipt.
    layout_integrity = build_layout_integrity_evidence(receipt)
    if not layout_integrity.get("passed"):
        return None

    arithmetic = {
        "summary_update_policy": "composed_catalog_totals",
        "new_subtotal": _format_money(subtotal),
        "new_taxable_subtotal": _format_money(taxable_subtotal),
        "tax_rate": _format_rate(rate) if rate is not None else None,
        "tax_basis": "taxable_catalog_subtotal",
        "tax_rate_stable": bool(rate_stable),
        "new_tax": _format_money(tax),
        "new_grand_total": _format_money(total),
        "subtotal_consistent": (subtotal + tax) == total,
        "updated_summary_labels": updated_labels,
    }
    grounding = {
        "entries": [entry.to_dict() for entry in entries],
        "all_priced": all(entry.price > Decimal("0.00") for entry in entries),
        "all_named": all(str(entry.name).strip() for entry in entries),
        "source": "merchant_online_catalog",
    }
    label_control = _verify_template_row_labels(receipt)

    return _candidate_from_receipt(
        receipt,
        merchant_name,
        source="merchant_online_catalog",
        operation="compose_online_catalog",
        index=index,
        metadata={
            "profile": _profile_summary(profile),
            "base_receipt_key": _receipt_key(scaffold.receipt),
            "layout_integrity": layout_integrity,
            "composed_item_count": len(entries),
            "composed_items": [entry.to_dict() for entry in entries],
            "online_catalog_grounding": grounding,
            "label_control": label_control,
            "arithmetic_reconciliation": arithmetic,
            "structure_similarity": _score_structure_similarity(
                _analyze_receipt(receipt),
                analyses,
            ),
            "new_subtotal": _format_money(subtotal),
            "new_tax": _format_money(tax),
            "new_grand_total": _format_money(total),
            "item_count_fields_reconciled": item_count_fields_updated,
            "balancing_strategy": (
                "compose a net-new receipt from online catalog rows; recompute "
                "subtotal, tax on taxable items at the merchant's stable observed "
                "rate, and the grand total"
            ),
        },
    )


def _write_composed_totals(
    receipt: dict[str, Any],
    subtotal: Decimal,
    tax: Decimal,
    total: Decimal,
    *,
    old_grand_total: Decimal | None = None,
) -> dict[str, int]:
    """Rewrite the scaffold's SUBTOTAL / TAX / GRAND_TOTAL amounts in place, plus
    any unlabeled footer copy of the old grand total (a paid/balance line), so a
    composed receipt never shows two conflicting totals."""
    counts = {
        "subtotal": 0,
        "tax": 0,
        "grand_total": 0,
        "payment_or_balance": 0,
    }
    tax_words: list[tuple[dict[str, Any], Decimal]] = []
    summary_labels = {"SUBTOTAL", "TAX", "GRAND_TOTAL"}
    for line in receipt.get("lines", []) or []:
        line_labels = {
            label
            for word in line.get("words", []) or []
            for label in (word.get("labels") or [])
            if label in summary_labels
        }
        for word in line.get("words", []) or []:
            labels = set(word.get("labels") or [])
            value = _parse_money(word.get("text"))
            if value is None:
                continue
            effective_labels = set(labels)
            if not effective_labels & {"LINE_TOTAL", *summary_labels}:
                effective_labels |= line_labels
            if "SUBTOTAL" in effective_labels:
                word["text"] = _format_money_like(word["text"], subtotal)
                _right_align_money_box(word)
                counts["subtotal"] += 1
            elif "TAX" in effective_labels:
                # Defer: a scaffold can show several TAX rows (e.g. state + city)
                # and writing the full composed tax into each would overstate it.
                tax_words.append((word, value))
            elif "GRAND_TOTAL" in effective_labels:
                word["text"] = _format_money_like(word["text"], total)
                _right_align_money_box(word)
                counts["grand_total"] += 1
            elif (
                old_grand_total is not None
                and value == old_grand_total
                and not labels & {"LINE_TOTAL", "TAX", "SUBTOTAL", "GRAND_TOTAL"}
            ):
                # An unlabeled paid/balance copy of the original grand total.
                word["text"] = _format_money_like(word["text"], total)
                _right_align_money_box(word)
                counts["payment_or_balance"] += 1
    # Distribute the composed tax across the scaffold's TAX rows (proportional to
    # their original split, remainder on the last) so the displayed tax lines sum
    # to exactly ``tax`` and stay consistent with the grand total.
    if tax_words:
        original_total = _money_sum(value for _, value in tax_words)
        running = Decimal("0.00")
        for index, (word, value) in enumerate(tax_words):
            if index == len(tax_words) - 1:
                share = _money(tax - running)
            elif original_total > Decimal("0.00"):
                share = _money(tax * value / original_total)
            else:
                share = Decimal("0.00")
            running += share
            word["text"] = _format_money_like(word["text"], share)
            _right_align_money_box(word)
            counts["tax"] += 1
    return counts


def _generate_compose_online_catalog_candidates(
    merchant_name: str,
    profile: dict[str, Any],
    analyses: list[MerchantAnalysis],
    online_catalog: list[OnlineCatalogEntry],
    *,
    start_index: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Compose up to ``limit`` distinct online-catalog receipts, high-fidelity
    first (the bundle gate drops any that are not), each on the cleanest base."""
    if limit <= 0:
        return []
    usable = [
        analysis
        for analysis in analyses
        if analysis.line_items
        and analysis.grand_total is not None
        and analysis.subtotal is not None
    ]
    if not usable:
        return []
    # The effective tax rate is a merchant property; derive it from EVERY receipt
    # with reliable subtotal/tax totals, not just the few with parsed line items
    # (that subset is small and can split the median between tax jurisdictions).
    rate, rate_stable, _ = _stable_tax_rate(analyses)
    # Without a stable observed rate we cannot put a realistic, internally
    # consistent tax on a composed taxable receipt, so skip the merchant rather
    # than print a guessed tax.
    if rate is None or not rate_stable:
        return []
    entries = list(online_catalog)
    if len(entries) < 2:
        return []
    # Cleanest base first so a noisy-footer scaffold never makes a clean
    # composition fail the catastrophic-splice budget on INHERITED overlap.
    scaffolds = sorted(
        usable,
        key=lambda analysis: (
            _base_overlap_count(analysis.receipt),
            _receipt_key(analysis.receipt),
        ),
    )
    max_items = min(len(entries), 5)
    built: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for step in range(limit * 4):
        scaffold = scaffolds[step % len(scaffolds)]
        item_count = 2 + (step % max(1, max_items - 1))
        chosen = _rotate(entries, step)[:item_count]
        key = (
            _receipt_key(scaffold.receipt),
            tuple(entry.name for entry in chosen),
        )
        if key in seen:
            continue
        seen.add(key)
        candidate = _compose_online_catalog_receipt(
            merchant_name,
            profile,
            # Score structure against the merchant's FULL real set so the
            # real-to-real baseline reflects true variation, not just the few
            # receipts whose line items happened to parse (a near-identical
            # subset would set an unreachably high baseline).
            analyses,
            scaffold,
            chosen,
            rate,
            rate_stable,
            index=start_index + len(built),
        )
        if candidate:
            built.append(candidate)
        if sum(_candidate_is_high_fidelity(c) for c in built) >= limit:
            break
    indexed = sorted(
        enumerate(built),
        key=lambda item: (
            _candidate_is_high_fidelity(item[1]),
            *_candidate_selection_key(item[1]),
            -item[0],
        ),
        reverse=True,
    )
    chosen_out: list[dict[str, Any]] = []
    for offset, (input_index, candidate) in enumerate(indexed[:limit]):
        _renumber_candidate(candidate, merchant_name, start_index + offset)
        _set_selection_evidence(
            candidate,
            candidate_count=len(built),
            selected_index=input_index,
        )
        chosen_out.append(candidate)
    return chosen_out


def _candidate_from_receipt(
    receipt: dict[str, Any],
    merchant_name: str,
    *,
    source: str,
    operation: str,
    index: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    # Universal store-identity cleaning: BEFORE flattening, replace this
    # candidate's (possibly garbled) printed store cluster with the receipt's own
    # real Google Places address/phone/website. Runs here because this is the
    # single exit every operation funnels through. compose_store_header is skipped
    # (it already swapped in a clean sibling branch); see normalize_store_identity.
    places_clean = normalize_store_identity(receipt, operation=operation)
    tokens, bboxes, tags = _flatten_lines(
        receipt.get("lines", []), merchant_name
    )
    # Single CONTENT-reconciliation model (synthesis_receipt_model): rewrite any
    # printed item-count line to the visible product-line count and audit the
    # subtotal/tax/grand-total cascade. The report rides on the candidate so the
    # generator can drop a totals-owning operation whose math still contradicts.
    tokens, bboxes, tags, content_report = reconcile_and_validate(
        tokens, bboxes, tags
    )
    slug = _slug(f"{merchant_name}-{source}-{operation}-{index}")
    candidate_metadata = {
        "source": source,
        "parameterization_version": "merchant-generic-v1",
        "operation": operation,
        "train_only_reason": (
            "Synthetic examples target known model confusions and must not enter validation."
        ),
        **metadata,
    }
    candidate_metadata.setdefault(
        "layout_integrity",
        build_layout_integrity_evidence(receipt),
    )
    candidate_metadata.setdefault(
        "candidate_quality",
        build_synthesis_candidate_quality(
            operation,
            candidate_metadata,
            token_count=len(tokens),
        ),
    )
    candidate_metadata.setdefault(
        "synthetic_receipt_preview",
        build_synthetic_receipt_preview(receipt, candidate_metadata),
    )
    candidate_metadata.setdefault(
        "synthesis_accuracy_evidence",
        build_synthesis_accuracy_evidence(operation, candidate_metadata),
    )
    candidate_metadata["content_reconciliation"] = content_report.to_dict()
    candidate_metadata["places_store_identity_clean"] = places_clean
    return {
        "candidate_id": slug,
        "recipe_id": f"{source}-{operation}",
        "merchant_name": merchant_name,
        "tokens": tokens,
        "bboxes": bboxes,
        "ner_tags": tags,
        "receipt_key": f"synthetic-{slug}#00001",
        "image_id": f"synthetic-{slug}",
        "train_only": True,
        "metadata": candidate_metadata,
    }


def build_synthetic_receipt_preview(
    receipt: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    *,
    max_lines: int = 48,
) -> dict[str, Any]:
    """Build a compact visual/text preview for UI and audit artifacts."""
    metadata = metadata or {}
    raw_lines = [
        line for line in receipt.get("lines", []) or [] if line.get("words")
    ]
    preview_lines = [
        _preview_line(line, line_number=index + 1, metadata=metadata)
        for index, line in enumerate(raw_lines[:max_lines])
    ]
    words = [
        word
        for line in raw_lines
        for word in line.get("words", []) or []
        if str(word.get("text") or "").strip()
    ]
    return {
        "coordinate_system": "normalized_receipt_0_1000_y_high_is_top",
        "line_count": len(raw_lines),
        "token_count": len(words),
        "truncated": len(raw_lines) > max_lines,
        "text": "\n".join(line["text"] for line in preview_lines),
        "lines": preview_lines,
    }


def build_layout_integrity_evidence(
    receipt: dict[str, Any],
    *,
    base_overlap_count: int | None = None,
    base_line_inversion_count: int | None = None,
) -> dict[str, Any]:
    """Validate generated receipt geometry before it can reach LayoutLM.

    Pass ``base_overlap_count`` / ``base_line_inversion_count`` (the pre-edit
    receipt's own counts) for edits that shift real rows without re-IDing them
    (remove/reflow), so an overlap or reading-order inversion the edit introduces
    between two real rows is caught even though it does not touch a synthetic
    line id, while inherited rotated-photo noise is still tolerated.
    """
    lines = [
        line for line in receipt.get("lines", []) or [] if line.get("words")
    ]
    words: list[dict[str, Any]] = []
    invalid_words: list[dict[str, Any]] = []
    out_of_bounds_words: list[dict[str, Any]] = []

    for line_index, line in enumerate(lines):
        for word_index, word in enumerate(line.get("words", []) or []):
            bbox = word.get("bbox")
            ref = {
                "text": str(word.get("text") or ""),
                "line_id": line.get("line_id"),
                "line_index": line_index,
                "word_index": word_index,
            }
            if not _valid_layout_box(bbox):
                invalid_words.append(ref)
                continue
            if not _box_in_layout_bounds(bbox):
                out_of_bounds_words.append({**ref, "bbox": list(bbox)})
                continue
            words.append({**ref, "bbox": list(bbox)})

    overlaps: list[dict[str, Any]] = []
    for left_index, left in enumerate(words):
        for right in words[left_index + 1 :]:
            if _boxes_have_significant_overlap(left["bbox"], right["bbox"]):
                overlaps.append(
                    {
                        "left_text": left["text"],
                        "right_text": right["text"],
                        "left_line_id": left.get("line_id"),
                        "right_line_id": right.get("line_id"),
                    }
                )

    # Real Apple Vision OCR receipts are photographed at a slight angle, so a
    # line's word centroids do not land on a perfectly monotonic y. Count an
    # inversion only when the next line sits a meaningful step HIGHER than the
    # current one (beyond _LINE_ORDER_EPSILON), so rotation near-ties are not
    # mistaken for scrambled reading order.
    line_ys = [_line_y(line) for line in lines]
    line_inversion_count = sum(
        1
        for index in range(len(line_ys) - 1)
        if line_ys[index] < line_ys[index + 1] - _LINE_ORDER_EPSILON
    )
    # An overlap that touches a SYNTHETIC line (inserted at/above the synthetic
    # id floor) is a collision the synthesis itself introduced (an added row
    # landing on a neighbor or a summary line) — always a hard defect, unlike the
    # mild base-OCR overlaps from a rotated photo that the budget tolerates.
    synthetic_overlap_count = sum(
        1
        for overlap in overlaps
        if _is_synthetic_line_id(overlap.get("left_line_id"))
        or _is_synthetic_line_id(overlap.get("right_line_id"))
    )
    line_order_valid = line_inversion_count == 0
    word_count = len(words) + len(invalid_words) + len(out_of_bounds_words)
    score = _layout_integrity_score_from_counts(
        overlap_count=len(overlaps),
        invalid_count=len(invalid_words),
        out_of_bounds_count=len(out_of_bounds_words),
        line_order_valid=line_order_valid,
        word_count=word_count,
        line_count=len(lines),
        line_inversion_count=line_inversion_count,
        synthetic_overlap_count=synthetic_overlap_count,
        base_overlap_count=base_overlap_count,
        base_line_inversion_count=base_line_inversion_count,
    )
    edit_introduced_overlap_count = (
        max(0, len(overlaps) - base_overlap_count)
        if base_overlap_count is not None
        else None
    )
    return {
        "schema_version": "synthetic-layout-integrity-v1",
        "score": score,
        "passed": score >= 1.0,
        "line_count": len(lines),
        "word_count": word_count,
        "overlap_pair_count": len(overlaps),
        "synthetic_overlap_pair_count": synthetic_overlap_count,
        "base_overlap_pair_count": base_overlap_count,
        "edit_introduced_overlap_pair_count": edit_introduced_overlap_count,
        "out_of_bounds_word_count": len(out_of_bounds_words),
        "invalid_word_box_count": len(invalid_words),
        "line_order_valid": line_order_valid,
        "line_inversion_count": line_inversion_count,
        "overlap_examples": overlaps[:5],
        "out_of_bounds_examples": out_of_bounds_words[:5],
        "invalid_word_examples": invalid_words[:5],
    }


def build_synthesis_accuracy_evidence(
    operation: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Summarize why a generated candidate is considered safe enough."""
    checks: list[str] = [
        "train_only_real_validation_policy",
        "merchant_local_geometry",
    ]
    structure = metadata.get("structure_similarity")
    if isinstance(structure, dict) and structure.get("score") is not None:
        checks.append("nearest_real_structure_similarity")
    layout = metadata.get("layout_integrity")
    if isinstance(layout, dict):
        checks.append("layout_integrity_checked")
        if layout.get("passed") is True:
            checks.append("no_overlapping_or_out_of_bounds_boxes")

    if operation == "add_line_item":
        observed = metadata.get("observed_item_evidence")
        arithmetic = metadata.get("arithmetic_reconciliation")
        added = metadata.get("added_item")
        if isinstance(observed, dict) and observed.get(
            "product_seen_outside_base"
        ):
            checks.append("item_seen_in_other_receipt")
        if isinstance(observed, dict) and observed.get(
            "base_receipt_has_category"
        ):
            checks.append("base_receipt_has_category")
        if isinstance(observed, dict) and _safe_int(
            observed.get("category_heading_seen_count")
        ):
            checks.append("category_heading_seen_in_real_receipts")
        if (
            isinstance(arithmetic, dict)
            and arithmetic.get("summary_update_policy")
            == "non_taxable_item_delta"
            and arithmetic.get("tax_delta") == "0.00"
        ):
            checks.append("non_taxable_arithmetic_reconciled")
        return {
            "operation": operation,
            "checks": checks,
            "changed_text": (
                added.get("product_text") if isinstance(added, dict) else None
            ),
            "category": (
                added.get("category") if isinstance(added, dict) else None
            ),
            "old_grand_total": metadata.get("old_grand_total"),
            "new_grand_total": metadata.get("new_grand_total"),
            "tax_delta": metadata.get("tax_delta"),
            "layout_integrity": _compact_layout_integrity_evidence(layout),
            "structure_similarity": _compact_structure_evidence(structure),
            "catalog_grounding": _compact_catalog_grounding_evidence(
                observed,
                added,
            ),
            "category_placement": _compact_category_placement_evidence(
                metadata,
                observed,
            ),
        }

    if operation == "remove_line_item":
        removed = metadata.get("removed_item")
        arithmetic = metadata.get("arithmetic_reconciliation")
        removal_context = metadata.get("removal_context")
        if (
            isinstance(arithmetic, dict)
            and arithmetic.get("summary_update_policy")
            == "non_taxable_item_delta"
            and arithmetic.get("tax_delta") == "0.00"
        ):
            checks.append("non_taxable_arithmetic_reconciled")
        if isinstance(removed, dict) and removed.get("taxable") is False:
            checks.append("removed_item_non_taxable")
        if (
            isinstance(removal_context, dict)
            and removal_context.get("category") != UNKNOWN_CATEGORY
            and (_safe_int(removal_context.get("category_item_count_before")) or 0) > 1
            and (_safe_int(removal_context.get("category_item_count_after")) or 0) >= 1
        ):
            checks.append("removed_from_multi_item_category")
        if isinstance(removal_context, dict) and (
            _safe_int(removal_context.get("shifted_line_count")) or 0
        ):
            checks.append("lower_lines_shifted_to_close_gap")
        return {
            "operation": operation,
            "checks": checks,
            "changed_text": (
                removed.get("product_text")
                if isinstance(removed, dict)
                else None
            ),
            "category": (
                removed.get("category") if isinstance(removed, dict) else None
            ),
            "old_grand_total": metadata.get("old_grand_total"),
            "new_grand_total": metadata.get("new_grand_total"),
            "tax_delta": metadata.get("tax_delta"),
            "layout_integrity": _compact_layout_integrity_evidence(layout),
            "structure_similarity": _compact_structure_evidence(structure),
            "removal_context": (
                removal_context
                if isinstance(removal_context, dict)
                else None
            ),
        }

    if operation == "replace_field":
        replacement = metadata.get("field_replacement")
        field = metadata.get("mutable_field_evidence")
        if isinstance(field, dict) and field.get("safe_to_mutate") is True:
            checks.append("field_marked_safe_to_mutate")
        if isinstance(field, dict) and field.get("stable_geometry") is True:
            checks.append("stable_field_geometry")
        if isinstance(field, dict) and field.get("stable_format"):
            checks.append("stable_field_format")
        return {
            "operation": operation,
            "checks": checks,
            "label": (
                replacement.get("label")
                if isinstance(replacement, dict)
                else None
            ),
            "old_text": (
                replacement.get("old_text")
                if isinstance(replacement, dict)
                else None
            ),
            "new_text": (
                replacement.get("new_text")
                if isinstance(replacement, dict)
                else None
            ),
            "format": (
                replacement.get("format")
                if isinstance(replacement, dict)
                else None
            ),
            "layout_integrity": _compact_layout_integrity_evidence(layout),
            "structure_similarity": _compact_structure_evidence(structure),
        }

    if operation == "hard_negative":
        checks.append("inserted_o_label_distractor")
        return {
            "operation": operation,
            "checks": checks,
            "actual_label": metadata.get("actual_label"),
            "predicted_label": metadata.get("predicted_label"),
            "error_kind": metadata.get("error_kind"),
            "layout_integrity": _compact_layout_integrity_evidence(layout),
            "structure_similarity": _compact_structure_evidence(structure),
        }

    return {
        "operation": operation,
        "checks": checks,
        "layout_integrity": _compact_layout_integrity_evidence(layout),
        "structure_similarity": _compact_structure_evidence(structure),
    }


def build_synthesis_candidate_quality(
    operation: str,
    metadata: dict[str, Any],
    *,
    token_count: int | None = None,
) -> dict[str, Any]:
    """Score candidate fidelity from evidence already used by hard gates."""
    structure = metadata.get("structure_similarity")
    structure_score = 0.0
    if isinstance(structure, dict):
        structure_score = _safe_float(structure.get("score"), 0.0)
    structure_gate = _structure_gate_details(structure)
    layout_integrity = _layout_integrity_score(
        metadata.get("layout_integrity")
    )

    components: dict[str, float] = {
        "structure_similarity": _bounded_score(structure_score),
        "structure_component_pass_rate": structure_gate["pass_rate"],
        "layout_integrity": layout_integrity,
        "token_budget": _token_budget_score(token_count),
    }
    weights: dict[str, float] = {
        "structure_similarity": 0.50,
        "structure_component_pass_rate": 0.10,
        "layout_integrity": 0.18,
        "token_budget": 0.22,
    }

    if operation == "add_line_item":
        observed = metadata.get("observed_item_evidence")
        added = metadata.get("added_item") or {}
        arithmetic = metadata.get("arithmetic_reconciliation")
        components.update(
            {
                "cross_receipt_grounding": _cross_receipt_grounding_score(
                    observed,
                    added,
                ),
                "category_alignment": _category_alignment_score(observed),
                "arithmetic_reconciliation": _arithmetic_reconciliation_score(
                    arithmetic,
                    expected_tax_delta="0.00",
                ),
                # An item inserted below the SUBTOTAL/TOTAL block is invalid.
                "valid_insertion_position": (
                    1.0
                    if added.get("insertion_position_valid", True)
                    else 0.0
                ),
            }
        )
        weights = {
            "structure_similarity": 0.22,
            "structure_component_pass_rate": 0.06,
            "layout_integrity": 0.10,
            "cross_receipt_grounding": 0.20,
            "category_alignment": 0.13,
            "arithmetic_reconciliation": 0.18,
            "valid_insertion_position": 0.06,
            "token_budget": 0.05,
        }
    elif operation == "remove_line_item":
        removed = metadata.get("removed_item")
        arithmetic = metadata.get("arithmetic_reconciliation")
        components.update(
            {
                "removed_item_safe": (
                    1.0
                    if isinstance(removed, dict)
                    and removed.get("taxable") is False
                    else 0.0
                ),
                "arithmetic_reconciliation": _arithmetic_reconciliation_score(
                    arithmetic,
                    expected_tax_delta="0.00",
                ),
            }
        )
        weights = {
            "structure_similarity": 0.32,
            "structure_component_pass_rate": 0.08,
            "layout_integrity": 0.12,
            "removed_item_safe": 0.22,
            "arithmetic_reconciliation": 0.24,
            "token_budget": 0.02,
        }
    elif operation == "replace_field":
        field = metadata.get("mutable_field_evidence")
        components.update(
            {
                "safe_mutable_field": (
                    1.0
                    if isinstance(field, dict)
                    and field.get("safe_to_mutate") is True
                    else 0.0
                ),
                "stable_field_geometry": (
                    1.0
                    if isinstance(field, dict)
                    and field.get("stable_geometry") is True
                    else 0.0
                ),
                "stable_field_format": (
                    1.0
                    if isinstance(field, dict) and field.get("stable_format")
                    else 0.0
                ),
            }
        )
        weights = {
            "structure_similarity": 0.26,
            "structure_component_pass_rate": 0.08,
            "layout_integrity": 0.10,
            "safe_mutable_field": 0.22,
            "stable_field_geometry": 0.16,
            "stable_field_format": 0.16,
            "token_budget": 0.02,
        }
    elif operation == "compose_online_catalog":
        grounding = metadata.get("online_catalog_grounding")
        grounding = grounding if isinstance(grounding, dict) else {}
        label_control = metadata.get("label_control")
        label_control = label_control if isinstance(label_control, dict) else {}
        arithmetic = metadata.get("arithmetic_reconciliation")
        arithmetic = arithmetic if isinstance(arithmetic, dict) else {}
        components.update(
            {
                # online content is grounded when every rendered row has a real
                # name and a real price
                "online_catalog_grounding": (
                    1.0
                    if grounding.get("all_priced") and grounding.get("all_named")
                    else 0.0
                ),
                # the differentiator vs cloning: every item token carries the
                # label we assigned
                "label_control": (
                    1.0 if label_control.get("all_correct") else 0.0
                ),
                # internally consistent totals, tax at a stable observed rate
                "arithmetic_reconciliation": (
                    1.0
                    if arithmetic.get("subtotal_consistent")
                    and arithmetic.get("tax_rate_stable")
                    else 0.0
                ),
            }
        )
        weights = {
            "structure_similarity": 0.24,
            "structure_component_pass_rate": 0.06,
            "layout_integrity": 0.12,
            "online_catalog_grounding": 0.18,
            "label_control": 0.22,
            "arithmetic_reconciliation": 0.16,
            "token_budget": 0.02,
        }
    elif operation == "compose_store_header":
        swap = metadata.get("store_header_swap")
        swap = swap if isinstance(swap, dict) else {}
        fields = swap.get("fields_swapped")
        fields = fields if isinstance(fields, list) else []
        swapped_labels = {
            str(field.get("label") or "").upper()
            for field in fields
            if isinstance(field, dict)
        }
        components.update(
            {
                "store_identity_coherence": (
                    1.0
                    if swap.get("merchant_match") is True
                    and swap.get("all_fields_from_single_place") is True
                    and str(swap.get("source_place_id") or "").strip()
                    and str(swap.get("source_place_id") or "").strip()
                    != str(swap.get("own_place_id") or "").strip()
                    else 0.0
                ),
                "address_swapped": (
                    1.0 if "ADDRESS_LINE" in swapped_labels else 0.0
                ),
                "store_field_swap_evidence": (
                    1.0 if bool(fields) and bool(swapped_labels) else 0.0
                ),
            }
        )
        weights = {
            "structure_similarity": 0.28,
            "structure_component_pass_rate": 0.08,
            "layout_integrity": 0.14,
            "store_identity_coherence": 0.22,
            "address_swapped": 0.18,
            "store_field_swap_evidence": 0.08,
            "token_budget": 0.02,
        }
    elif operation == "hard_negative":
        components.update(
            {
                "target_label_slot": (
                    1.0
                    if metadata.get("actual_label") == "O"
                    and metadata.get("predicted_label")
                    else 0.0
                ),
                "local_distractor": (1.0 if metadata.get("mutation") else 0.0),
            }
        )
        weights = {
            "structure_similarity": 0.36,
            "structure_component_pass_rate": 0.08,
            "layout_integrity": 0.12,
            "target_label_slot": 0.22,
            "local_distractor": 0.16,
            "token_budget": 0.06,
        }

    score = round(
        sum(
            components.get(name, 0.0) * weight
            for name, weight in weights.items()
        ),
        3,
    )
    operation_specific_gate = True
    if operation == "compose_online_catalog":
        operation_specific_gate = (
            components.get("online_catalog_grounding", 0.0) >= 1.0
            and components.get("label_control", 0.0) >= 1.0
            and components.get("arithmetic_reconciliation", 0.0) >= 1.0
        )
    elif operation == "compose_store_header":
        operation_specific_gate = (
            components.get("store_identity_coherence", 0.0) >= 1.0
            and components.get("address_swapped", 0.0) >= 1.0
            and components.get("store_field_swap_evidence", 0.0) >= 1.0
        )
    return {
        "schema_version": "synthetic-candidate-quality-v1",
        "score": score,
        "high_fidelity": (
            score >= 0.75
            and components["structure_similarity"]
            >= SYNTHETIC_MIN_STRUCTURE_SIMILARITY
            and components["layout_integrity"] >= 1.0
            and components.get("valid_insertion_position", 1.0) >= 1.0
            and structure_gate["passed"] is True
            and operation_specific_gate
        ),
        "components": {
            key: round(value, 3) for key, value in sorted(components.items())
        },
        "structure_gate": structure_gate,
        "selection_policy": (
            "Prefer merchant-local candidates with strong nearest-real structure, "
            "cross-receipt grounding for item additions, arithmetic reconciliation "
            "for total-changing edits, and stable geometry for field replacements."
        ),
    }


def select_high_fidelity_synthesis_candidate(
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the best feasible mutation using the same fidelity evidence as gates."""
    if not candidates:
        return None

    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (*_candidate_selection_key(item[1]), -item[0]),
        reverse=True,
    )
    selected_index, selected = ranked[0]
    metadata = selected.get("metadata")
    if not isinstance(metadata, dict):
        return selected

    _set_selection_evidence(
        selected,
        candidate_count=len(candidates),
        selected_index=selected_index,
    )
    return selected


_CANDIDATE_RANKED_BY = [
    "candidate_quality.high_fidelity",
    "real_baseline_comparison.within_real_score_range",
    "candidate_quality.score",
    "real_baseline_comparison.delta_from_min",
    "structure_similarity.score",
    "structure_component_pass_rate",
    "layout_integrity",
    "token_budget",
]


def _set_selection_evidence(
    candidate: dict[str, Any],
    *,
    candidate_count: int,
    selected_index: int,
) -> None:
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        return
    metadata["selection_evidence"] = {
        "schema_version": "synthetic-candidate-selection-v1",
        "selected_from_candidate_count": candidate_count,
        "selected_input_index": selected_index,
        "ranked_by": list(_CANDIDATE_RANKED_BY),
        "selected_score": _candidate_selection_summary(candidate),
        "selection_policy": (
            "Generate feasible merchant-local mutations, then keep the highest "
            "fidelity option instead of maximizing synthetic volume."
        ),
    }


def _candidate_selection_key(candidate: dict[str, Any]) -> tuple[float, ...]:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    quality = metadata.get("candidate_quality")
    quality = quality if isinstance(quality, dict) else {}
    components = quality.get("components")
    components = components if isinstance(components, dict) else {}
    structure = metadata.get("structure_similarity")
    structure = structure if isinstance(structure, dict) else {}
    baseline = structure.get("real_baseline_comparison")
    baseline = baseline if isinstance(baseline, dict) else {}

    within_baseline = baseline.get("within_real_score_range")
    baseline_signal = 0.5
    if within_baseline is True:
        baseline_signal = 1.0
    elif within_baseline is False:
        baseline_signal = 0.0

    return (
        1.0 if quality.get("high_fidelity") is True else 0.0,
        baseline_signal,
        _bounded_score(quality.get("score")),
        _safe_float(baseline.get("delta_from_min"), 0.0),
        _bounded_score(structure.get("score")),
        _bounded_score(components.get("structure_component_pass_rate")),
        _bounded_score(components.get("layout_integrity")),
        _bounded_score(components.get("token_budget")),
        -float(len(candidate.get("tokens") or [])),
    )


def _candidate_selection_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    quality = metadata.get("candidate_quality")
    quality = quality if isinstance(quality, dict) else {}
    components = quality.get("components")
    components = components if isinstance(components, dict) else {}
    structure = metadata.get("structure_similarity")
    structure = structure if isinstance(structure, dict) else {}
    baseline = structure.get("real_baseline_comparison")
    baseline = baseline if isinstance(baseline, dict) else {}
    summary = {
        "candidate_quality": _safe_float(quality.get("score"), 0.0),
        "high_fidelity": quality.get("high_fidelity") is True,
        "structure_similarity": _safe_float(structure.get("score"), 0.0),
        "structure_component_pass_rate": _safe_float(
            components.get("structure_component_pass_rate"),
            0.0,
        ),
        "layout_integrity": _safe_float(
            components.get("layout_integrity"),
            0.0,
        ),
        "token_budget": _safe_float(components.get("token_budget"), 0.0),
        "within_real_score_range": baseline.get("within_real_score_range"),
        "delta_from_min": _maybe_float(baseline.get("delta_from_min")),
        "baseline_pair_count": _safe_int(baseline.get("baseline_pair_count")),
        "token_count": len(candidate.get("tokens") or []),
    }
    return {
        key: value
        for key, value in summary.items()
        if value not in (None, "", [], {})
    }


def _structure_gate_details(structure: Any) -> dict[str, Any]:
    structure = structure if isinstance(structure, dict) else {}
    structure_score = _bounded_score(structure.get("score"))
    raw_components = structure.get("components")
    components = raw_components if isinstance(raw_components, dict) else {}

    passed_components: list[str] = []
    failed_components: dict[str, dict[str, float]] = {}
    missing_components: list[str] = []
    for (
        component,
        threshold,
    ) in SYNTHETIC_STRUCTURE_COMPONENT_THRESHOLDS.items():
        value = _safe_float(components.get(component), None)
        if value is None:
            missing_components.append(component)
            continue
        if value >= threshold:
            passed_components.append(component)
        else:
            failed_components[component] = {
                "value": round(value, 3),
                "threshold": threshold,
            }

    threshold_count = len(SYNTHETIC_STRUCTURE_COMPONENT_THRESHOLDS)
    pass_rate = round(len(passed_components) / threshold_count, 3)
    score_passed = structure_score >= SYNTHETIC_MIN_STRUCTURE_SIMILARITY
    return {
        "min_structure_similarity": SYNTHETIC_MIN_STRUCTURE_SIMILARITY,
        "structure_similarity_passed": score_passed,
        "component_thresholds": SYNTHETIC_STRUCTURE_COMPONENT_THRESHOLDS,
        "passed_components": passed_components,
        "failed_components": failed_components,
        "missing_components": missing_components,
        "pass_rate": pass_rate,
        "passed": (
            score_passed and not failed_components and not missing_components
        ),
    }


def _bounded_score(value: Any) -> float:
    return max(0.0, min(1.0, _safe_float(value, 0.0)))


def _layout_integrity_score(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    score = _maybe_float(value.get("score"))
    if score is not None:
        return _bounded_score(score)
    return 1.0 if value.get("passed") is True else 0.0


def _layout_integrity_score_from_counts(
    *,
    overlap_count: int,
    invalid_count: int,
    out_of_bounds_count: int,
    line_order_valid: bool,
    word_count: int = 0,
    line_count: int = 0,
    line_inversion_count: int = 0,
    synthetic_overlap_count: int = 0,
    base_overlap_count: int | None = None,
    base_line_inversion_count: int | None = None,
) -> float:
    # layout_integrity measures geometry the SYNTHESIS introduced, not the base
    # receipt's own quality. Hard failures: a malformed / off-canvas box, or a
    # collision involving an inserted synthetic line (a new row landing on a
    # neighbor / summary line).
    if invalid_count or out_of_bounds_count or synthetic_overlap_count:
        return 0.0
    # When the base receipt's own overlap count is known, any INCREASE is an
    # overlap the edit introduced — e.g. a remove/reflow that shifted two real
    # rows (unchanged line IDs, so synthetic_overlap_count misses it) onto each
    # other. Inherited base overlaps are tolerated; new ones are not.
    if base_overlap_count is not None and overlap_count > base_overlap_count:
        return 0.0
    # Likewise for reading order: a tilted real photo carries inherited
    # adjacent-line inversions (beyond the rotation epsilon), so only an edit
    # that INCREASES the inversion count — a reflow that scrambled rows — fails.
    if (
        base_line_inversion_count is not None
        and line_inversion_count > base_line_inversion_count
    ):
        return 0.0
    # Base-OCR overlaps and line inversions are INHERITED from the real receipt
    # (it carries the same rotated-photo noise), so they must NOT fail a clean
    # edit — penalizing them rejected well-formed add_line_item rows just for
    # sitting on a slightly noisy base. Only a CATASTROPHIC base defect — a
    # concatenated-receipt splice with overlaps on the order of the word count —
    # is rejected.
    if word_count and overlap_count > max(8, word_count // 2):
        return 0.0
    return 1.0


# Normalized-y (0..1) tolerance for line-order inversions: roughly half a line
# height, so rotation near-ties between adjacent lines are not counted as a
# reading-order inversion.
_LINE_ORDER_EPSILON = 0.01

# Inserted synthetic lines carry ids at/above this base (see _build_line and
# _clone_row_group_lines); an overlap touching one is a synthesis collision.
_SYNTHETIC_LINE_ID_BASE = 20_000
# Online-catalog template fill inserts rows under this lower base; add-item
# cloning uses _SYNTHETIC_LINE_ID_BASE (+ a per-line offset for multi-line
# bands). Any id at or above this floor is a generator-inserted row.
_SYNTHETIC_LINE_ID_FLOOR = 10_000


def _is_synthetic_line_id(value: Any) -> bool:
    """True for any generator-inserted line/word id (add-item or online-catalog
    template fill), including the per-line offsets a multi-line band uses."""
    try:
        return int(value) >= _SYNTHETIC_LINE_ID_FLOOR
    except (TypeError, ValueError):
        return False


def _token_budget_score(token_count: int | None) -> float:
    if token_count is None:
        return 0.75
    if token_count <= 180:
        return 1.0
    if token_count >= 220:
        return 0.0
    return max(0.0, 1.0 - ((token_count - 180) / 40.0))


def _cross_receipt_grounding_score(observed: Any, item: Any) -> float:
    observed = observed if isinstance(observed, dict) else {}
    item = item if isinstance(item, dict) else {}
    if observed.get("product_seen_outside_base") or item.get(
        "seen_in_other_receipt"
    ):
        return 1.0
    if observed.get("product_seen_in_receipts") or item.get(
        "source_receipt_keys"
    ):
        return 0.5
    return 0.0


def _category_alignment_score(observed: Any) -> float:
    if not isinstance(observed, dict):
        return 0.0
    score = 0.0
    if observed.get("category"):
        score += 0.30
    if observed.get("base_receipt_has_category") is True:
        score += 0.35
    if _safe_int(observed.get("category_seen_count")):
        score += 0.20
    if _safe_int(observed.get("category_heading_seen_count")):
        score += 0.15
    return min(1.0, score)


def _arithmetic_reconciliation_score(
    arithmetic: Any,
    *,
    expected_tax_delta: str,
) -> float:
    if not isinstance(arithmetic, dict):
        return 0.0
    score = 0.0
    if arithmetic.get("summary_update_policy") == "non_taxable_item_delta":
        score += 0.35
    if str(arithmetic.get("tax_delta") or "") == expected_tax_delta:
        score += 0.25
    updated = arithmetic.get("updated_summary_labels")
    if isinstance(updated, dict):
        if _safe_int(updated.get("grand_total")):
            score += 0.20
        if _safe_int(updated.get("subtotal")):
            score += 0.10
        if _safe_int(updated.get("payment_or_balance")):
            score += 0.10
    return min(1.0, score)


def _preview_line(
    line: dict[str, Any],
    *,
    line_number: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    words = sorted(
        [word for word in line.get("words", []) or [] if word.get("text")],
        key=lambda word: (
            _safe_float((word.get("bbox") or [0])[0], 0.0),
            _safe_int(word.get("word_id")) or 0,
        ),
    )
    labels = sorted(
        {
            label
            for word in words
            for label in (word.get("labels") or [])
            if label
        }
    )
    text = " ".join(str(word.get("text") or "") for word in words).strip()
    field_replacement = metadata.get("field_replacement")
    replacement_label = (
        str(field_replacement.get("label") or "")
        if isinstance(field_replacement, dict)
        else ""
    )
    replacement_text = (
        str(field_replacement.get("new_text") or "")
        if isinstance(field_replacement, dict)
        else ""
    )
    modified_labels = (
        [replacement_label]
        if replacement_label
        and replacement_label in labels
        and replacement_text
        and any(
            str(word.get("text") or "") == replacement_text for word in words
        )
        else []
    )
    return {
        "line_number": line_number,
        "line_id": line.get("line_id"),
        "y": round(_line_y(line), 4),
        "text": text,
        "labels": labels,
        "role": _preview_line_role(text, labels),
        "bbox": _preview_line_bbox(words),
        "synthetic_insert": _is_synthetic_insert_id(line.get("line_id"))
        or any(_is_synthetic_insert_id(word.get("line_id")) for word in words),
        "modified_labels": modified_labels,
    }


def _is_synthetic_insert_id(value: Any) -> bool:
    return value in {10_000, 20_000}


def _preview_line_bbox(words: list[dict[str, Any]]) -> list[int] | None:
    boxes = [
        word.get("bbox")
        for word in words
        if isinstance(word.get("bbox"), list) and len(word["bbox"]) == 4
    ]
    if not boxes:
        return None
    return [
        min(int(box[0]) for box in boxes),
        min(int(box[1]) for box in boxes),
        max(int(box[2]) for box in boxes),
        max(int(box[3]) for box in boxes),
    ]


def _preview_line_role(text: str, labels: list[str]) -> str:
    label_set = set(labels)
    if label_set & {"PRODUCT_NAME", "LINE_TOTAL", "UNIT_PRICE", "QUANTITY"}:
        return "line_item"
    if label_set & {"SUBTOTAL", "TAX", "GRAND_TOTAL", "DISCOUNT", "COUPON"}:
        return "summary"
    if label_set & {
        "MERCHANT_NAME",
        "ADDRESS_LINE",
        "PHONE_NUMBER",
        "WEBSITE",
        "STORE_HOURS",
        "DATE",
        "TIME",
    }:
        return "header"
    if text.strip().upper().strip(":") in GENERIC_CATEGORY_HEADINGS:
        return "category_heading"
    return "context"


def _compact_structure_evidence(structure: Any) -> dict[str, Any] | None:
    if not isinstance(structure, dict):
        return None
    result = {
        "score": structure.get("score"),
        "nearest_real_receipt_key": structure.get("nearest_real_receipt_key"),
        "components": structure.get("components") or {},
    }
    shape_deltas = structure.get("shape_deltas")
    if isinstance(shape_deltas, dict):
        result["shape_deltas"] = shape_deltas
    match_summary = structure.get("match_summary")
    if isinstance(match_summary, dict):
        result["match_summary"] = match_summary
    baseline = structure.get("real_baseline_comparison")
    if isinstance(baseline, dict):
        result["real_baseline_comparison"] = baseline
    return result


def _compact_layout_integrity_evidence(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        "score": _maybe_float(value.get("score")),
        "passed": value.get("passed") is True,
        "line_count": _safe_int(value.get("line_count")),
        "word_count": _safe_int(value.get("word_count")),
        "overlap_pair_count": _safe_int(value.get("overlap_pair_count")),
        "out_of_bounds_word_count": _safe_int(
            value.get("out_of_bounds_word_count")
        ),
        "invalid_word_box_count": _safe_int(
            value.get("invalid_word_box_count")
        ),
        "line_order_valid": value.get("line_order_valid") is True,
    }
    for key in (
        "overlap_examples",
        "out_of_bounds_examples",
        "invalid_word_examples",
    ):
        examples = value.get(key)
        if isinstance(examples, list) and examples:
            result[key] = examples[:3]
    return {
        key: item
        for key, item in result.items()
        if item not in (None, "", [], {})
    }


def _compact_catalog_grounding_evidence(
    observed: Any,
    item: Any,
) -> dict[str, Any] | None:
    if not isinstance(observed, dict):
        return None
    item = item if isinstance(item, dict) else {}
    product_sources = [
        str(source)
        for source in observed.get("product_seen_in_receipts") or []
        if source
    ]
    outside_sources = [
        str(source)
        for source in observed.get("product_seen_outside_base") or []
        if source
    ]
    category_sources = [
        str(source)
        for source in observed.get("category_seen_in_receipts") or []
        if source
    ]
    product_observed_count = _safe_int(
        observed.get("product_observed_count")
        or item.get("observed_count")
        or item.get("count")
    )
    result = {
        "product_observed_count": product_observed_count,
        "product_seen_receipt_count": len(product_sources) or None,
        "product_seen_outside_base_count": len(outside_sources),
        "product_seen_outside_base": outside_sources[:3],
        "category": observed.get("category") or item.get("category"),
        "category_seen_count": _safe_int(observed.get("category_seen_count")),
        "category_heading_seen_count": _safe_int(
            observed.get("category_heading_seen_count")
        ),
        "category_seen_in_receipts": category_sources[:3],
    }
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [])
    }


def _compact_category_placement_evidence(
    metadata: dict[str, Any],
    observed: Any,
) -> dict[str, Any] | None:
    insertion = metadata.get("category_insertion")
    observed = observed if isinstance(observed, dict) else {}
    result: dict[str, Any] = {}
    if isinstance(insertion, dict):
        same_category_section = insertion.get("same_category_section")
        result.update(
            {
                "category": insertion.get("category"),
                "insert_y": insertion.get("y_center"),
                "shifted_lower_lines_by": _safe_int(
                    insertion.get("shifted_lower_lines_by")
                ),
                "shifted_line_count": _safe_int(
                    insertion.get("shifted_line_count")
                ),
                "shifted_lower_line_shift_min": _safe_int(
                    insertion.get("shifted_lower_line_shift_min")
                ),
                "shifted_lower_line_shift_max": _safe_int(
                    insertion.get("shifted_lower_line_shift_max")
                ),
                "line_step": _safe_int(insertion.get("line_step")),
                "category_item_count_before": _safe_int(
                    insertion.get("category_item_count_before")
                ),
                "nearest_category_item_y": insertion.get("nearest_category_item_y"),
                "nearest_lower_line_y": insertion.get("nearest_lower_line_y"),
                "same_category_section": (
                    same_category_section
                    if isinstance(same_category_section, bool)
                    else None
                ),
                "selection_reason": insertion.get("selection_reason"),
            }
        )
    elif observed:
        result["category"] = observed.get("category")

    if observed:
        base_has_category = observed.get("base_receipt_has_category") is True
        result.update(
            {
                "base_receipt_has_category": base_has_category,
                "category_seen_count": _safe_int(
                    observed.get("category_seen_count")
                ),
                "category_heading_seen_count": _safe_int(
                    observed.get("category_heading_seen_count")
                ),
                "category_alignment": (
                    "same_category_as_base"
                    if base_has_category
                    else "category_unverified_on_base"
                ),
            }
        )

    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [])
    } or None


def _normalize_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    source_lines = receipt.get("all_lines") or receipt.get("lines", []) or []
    for line_index, line in enumerate(source_lines):
        line_words = []
        line_y = _safe_float(line.get("y"), 0.5)
        line_id = int(line.get("line_id") or line_index + 1)
        for word_index, word in enumerate(line.get("words", []) or []):
            text = str(word.get("text") or "").strip()
            if not text:
                continue
            labels = [
                label
                for label in (
                    _normalize_label(label)
                    for label in (word.get("labels") or [])
                )
                if label != "O"
            ]
            row = {
                "text": text,
                "bbox": _coerce_bbox(word.get("bbox"), word.get("x"), line_y),
                "labels": labels,
                "line_id": line_id,
                "word_id": int(word.get("word_id") or word_index + 1),
            }
            line_words.append(row)
            words.append(row)
        if line_words:
            line_words.sort(key=lambda row: (row["bbox"][0], row["word_id"]))
            lines.append(
                {"line_id": line_id, "y": line_y, "words": line_words}
            )

    lines.sort(key=lambda line: -_line_y(line))
    return {
        "receipt_id": receipt.get("receipt_id"),
        "image_id": receipt.get("image_id"),
        "receipt_num": receipt.get("receipt_num"),
        "lines": lines,
        "words": words,
        # Carry the cached Google Places record (if attached upstream) so the
        # compose_store_header op can swap this receipt's store cluster for a
        # different branch of the same merchant.
        "receipt_place": receipt.get("receipt_place"),
        "place_id": receipt.get("place_id")
        or (receipt.get("receipt_place") or {}).get("place_id"),
        # The merchant's full branch pool (incl. fetched siblings) for header
        # composition; falls back to this receipt's own place when absent.
        "merchant_place_pool": receipt.get("merchant_place_pool"),
    }


# Labels that belong to an item's vertical band (its name/total and per-row
# satellites). A line carrying any OTHER label is a header or summary line and
# acts as a band barrier.
_ITEM_BODY_LABELS = {"PRODUCT_NAME", "LINE_TOTAL", "QUANTITY", "UNIT_PRICE"}
# Summary / payment keywords that mark a band barrier even when the OCR left the
# row unlabeled (so a bare "SUBTOTAL 12.34" still stops band expansion).
_SUMMARY_TEXT_BARRIER_TOKENS = {
    "SUBTOTAL",
    "TOTAL",
    "TAX",
    "BALANCE",
    "CHANGE",
    "DISCOUNT",
    "COUPON",
    "TENDER",
    "DUE",
}


def _segment_item_bands(
    receipt: dict[str, Any], line_items: list[MerchantLineItem]
) -> None:
    """Assign each line item its full vertical band.

    Every receipt line is attributed to the nearest line item whose labeled
    (name/total) line it sits beside, without crossing a category heading or a
    summary line (SUBTOTAL/TAX/GRAND_TOTAL). An item's band is then the
    contiguous line range attributed to it, and its pixel extent spans those
    lines' word boxes — recovering the code/flag/quantity rows the name+total
    labels miss, so add/remove/clone can act on whole items.
    """
    lines = receipt.get("lines") or []
    count = len(lines)
    owner: dict[int, MerchantLineItem] = {}
    for item in line_items:
        for index in item.line_indices:
            if 0 <= index < count:
                owner[index] = item

    def is_barrier(index: int) -> bool:
        line = lines[index]
        if _line_category_heading(line):
            return True
        labels = {
            label
            for word in line.get("words", [])
            for label in (word.get("labels") or [])
        }
        # A line that carries ONLY non-item labels — header fields like
        # MERCHANT_NAME / DATE / TIME / ADDRESS, or summary fields like
        # SUBTOTAL / TAX / GRAND_TOTAL / DISCOUNT / COUPON — never belongs to an
        # item's band, so it must stop band expansion. Without this a header row
        # above the first item (no category heading) would be swept into that
        # item and could be cloned or deleted with it.
        if labels and not (labels & _ITEM_BODY_LABELS):
            return True
        # Summary / adjustment rows whose words are UNLABELED still must not be
        # attributed to an item (e.g. an OCR'd "SUBTOTAL 12.34" or "TOTAL 13.10"
        # with no labels); otherwise whole-band removal can delete a totals line.
        texts = {
            str(word.get("text") or "").strip().upper().strip(":")
            for word in line.get("words", [])
        }
        return bool(texts & _SUMMARY_TEXT_BARRIER_TOKENS)

    attribution: dict[int, MerchantLineItem] = dict(owner)
    for index in range(count):
        if index in owner or is_barrier(index):
            continue
        nearest: MerchantLineItem | None = None
        nearest_dist: int | None = None
        # Lines are ordered top-to-bottom, so direction +1 is the FOLLOWING item
        # in reading order. Scan it first so an unlabeled satellite equidistant
        # between two items (e.g. previous item's price, then this item's code
        # row) is attributed to the item it actually heads, not the one above.
        for direction in (1, -1):
            cursor, dist = index + direction, 1
            while 0 <= cursor < count:
                if is_barrier(cursor):
                    break
                if cursor in owner:
                    if nearest_dist is None or dist < nearest_dist:
                        nearest_dist, nearest = dist, owner[cursor]
                    break
                cursor += direction
                dist += 1
        if nearest is not None:
            attribution[index] = nearest

    by_item: dict[int, list[int]] = defaultdict(list)
    for index, item in attribution.items():
        by_item[id(item)].append(index)

    for item in line_items:
        idxs = sorted(by_item.get(id(item)) or list(item.line_indices))
        band = list(range(min(idxs), max(idxs) + 1))
        ys: list[float] = []
        for index in band:
            for word in lines[index].get("words", []):
                box = word.get("bbox")
                if box:
                    ys.extend((float(box[1]), float(box[3])))
        item.band_line_indices = band
        item.band_top_y = max(ys) if ys else item.center_y
        item.band_bottom_y = min(ys) if ys else item.center_y


def _analyze_receipt(receipt: dict[str, Any]) -> MerchantAnalysis:
    product_rows: list[dict[str, Any]] = []
    total_rows: list[dict[str, Any]] = []
    line_items: list[MerchantLineItem] = []
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    grand_total: Decimal | None = None
    grand_total_line_indices: list[int] = []
    current_category = UNKNOWN_CATEGORY
    category_sequence: list[str] = []
    category_by_line: dict[int, str] = {}

    for line_index, line in enumerate(receipt.get("lines", [])):
        heading = _line_category_heading(line)
        if heading:
            current_category = heading
            category_sequence.append(heading)
        category_by_line[line_index] = current_category

        product_words = [
            word
            for word in line.get("words", [])
            if "PRODUCT_NAME" in word.get("labels", [])
        ]
        total_words = [
            word
            for word in line.get("words", [])
            if "LINE_TOTAL" in word.get("labels", [])
            and _parse_money(word.get("text")) is not None
        ]
        if product_words:
            product_rows.append(
                {
                    "line_index": line_index,
                    "line": line,
                    "product_words": product_words,
                    "center_y": _line_y(line) * 1000,
                    "category": current_category,
                }
            )
        if total_words:
            total_rows.append(
                {
                    "line_index": line_index,
                    "line": line,
                    "amount": _parse_money(total_words[-1].get("text")),
                    "center_y": _line_y(line) * 1000,
                    "category": current_category,
                }
            )

        if any(
            "GRAND_TOTAL" in word.get("labels", [])
            for word in line.get("words", [])
        ):
            parsed = [
                _parse_money(word.get("text"))
                for word in line.get("words", [])
                if _parse_money(word.get("text")) is not None
            ]
            if parsed:
                grand_total_line_indices.append(line_index)
                if grand_total is None:
                    grand_total = parsed[-1]
        if any(
            "SUBTOTAL" in word.get("labels", [])
            for word in line.get("words", [])
        ):
            parsed = [
                _parse_money(word.get("text"))
                for word in line.get("words", [])
                if _parse_money(word.get("text")) is not None
            ]
            if parsed and subtotal is None:
                subtotal = parsed[-1]
        if any(
            "TAX" in word.get("labels", []) for word in line.get("words", [])
        ):
            parsed = [
                _parse_money(word.get("text"))
                for word in line.get("words", [])
                if _parse_money(word.get("text")) is not None
            ]
            if parsed:
                tax_total = _money_sum(
                    [tax_total or Decimal("0.00"), parsed[-1]]
                )

    used_total_lines: set[int] = set()
    for product_row in product_rows:
        total_row = _match_total_row(product_row, total_rows, used_total_lines)
        if total_row is None or total_row["amount"] is None:
            continue
        used_total_lines.add(total_row["line_index"])
        category = product_row["category"]
        if category == UNKNOWN_CATEGORY:
            category = total_row["category"]
        line_items.append(
            MerchantLineItem(
                line_index=product_row["line_index"],
                line_indices=sorted(
                    {product_row["line_index"], total_row["line_index"]}
                ),
                amount=total_row["amount"],
                product_text=" ".join(
                    word["text"] for word in product_row["product_words"]
                ),
                center_y=statistics.median(
                    [product_row["center_y"], total_row["center_y"]]
                ),
                # Classify taxability over the FULL contiguous row group, not
                # just the labeled product/total lines: a tax flag ('T') can sit
                # on its own line between them. Missing it mislabels a taxable
                # item as non-taxable, which then becomes a frozen-tax add whose
                # cloned row visibly carries a 'T' flag (a contradiction).
                taxable=_line_is_taxable(
                    *_span_lines(
                        receipt,
                        product_row["line_index"],
                        total_row["line_index"],
                    )
                ),
                category=category
                or category_by_line.get(
                    product_row["line_index"], UNKNOWN_CATEGORY
                ),
            )
        )

    _segment_item_bands(receipt, line_items)
    return MerchantAnalysis(
        receipt=receipt,
        line_items=line_items,
        subtotal=subtotal,
        tax_total=tax_total,
        grand_total=grand_total,
        grand_total_line_indices=grand_total_line_indices,
        category_sequence=category_sequence,
    )


def _capture_row_group(
    analysis: MerchantAnalysis, item: MerchantLineItem
) -> dict[str, Any] | None:
    """Capture an item's verbatim real line group from its source receipt.

    line_indices only marks the labeled product and price rows; an item's full
    visual row can also include unlabeled rows between them (a tax-flag line, a
    quantity multiplier). Capture every line from the first to the last labeled
    line inclusive so the merchant's complete row grammar is preserved.
    """
    lines = analysis.receipt.get("lines") or []
    idxs = item.band_line_indices or item.line_indices or [item.line_index]
    lo, hi = min(idxs), max(idxs)
    captured: list[dict[str, Any]] = []
    for li in range(lo, hi + 1):
        if not 0 <= li < len(lines):
            continue
        src = lines[li]
        words = [
            {
                "text": str(word.get("text") or ""),
                "bbox": list(word.get("bbox") or []),
                "labels": list(word.get("labels") or []),
            }
            for word in src.get("words") or []
            if word.get("bbox") and str(word.get("text") or "").strip()
        ]
        if words:
            captured.append({"y": _line_y(src), "words": words})
    if not captured:
        return None
    return {"lines": captured, "amount": str(item.amount)}


def _build_item_catalog(
    analyses: list[MerchantAnalysis],
) -> list[MerchantCatalogEntry]:
    grouped: dict[tuple[str, str, bool], dict[str, Any]] = {}
    for analysis in analyses:
        receipt_key = _receipt_key(analysis.receipt)
        for item in analysis.line_items:
            if not _is_catalog_item(item):
                continue
            key = (
                item.category,
                _normalize_product_text(item.product_text),
                item.taxable,
            )
            grouped.setdefault(
                key,
                {
                    "product_text": item.product_text,
                    "amounts": [],
                    "source_receipt_keys": set(),
                    "source_rows": {},
                },
            )
            grouped[key]["amounts"].append(item.amount)
            grouped[key]["source_receipt_keys"].add(receipt_key)
            if receipt_key not in grouped[key]["source_rows"]:
                captured = _capture_row_group(analysis, item)
                if captured:
                    grouped[key]["source_rows"][receipt_key] = captured

    entries = [
        MerchantCatalogEntry(
            product_text=value["product_text"],
            amount=_median_money(value["amounts"]),
            category=category,
            taxable=taxable,
            count=len(value["amounts"]),
            source_receipt_keys=sorted(value["source_receipt_keys"]),
            source_rows=value["source_rows"],
        )
        for (category, _product, taxable), value in grouped.items()
    ]
    entries.sort(
        key=lambda entry: (
            entry.category == UNKNOWN_CATEGORY,
            -entry.count,
            entry.product_text,
        )
    )
    return entries


def _label_slot(
    receipts: list[dict[str, Any]], label: str
) -> dict[str, Any] | None:
    words = [
        word
        for receipt in receipts
        for word in receipt.get("words", [])
        if label in word.get("labels", [])
    ]
    if not words:
        return None
    return {
        "x": _summary([_cx(word["bbox"]) for word in words]).to_dict(),
        "y": _summary([_cy(word["bbox"]) for word in words]).to_dict(),
        "examples": _dedupe(word["text"] for word in words)[:8],
    }


def _summarize_mutable_fields(
    label_slots: dict[str, Any],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for label in ("DATE", "TIME"):
        slot = label_slots.get(label)
        if not isinstance(slot, dict):
            continue
        examples = [str(value) for value in slot.get("examples") or []]
        patterns = Counter(
            pattern
            for example in examples
            if (pattern := _datetime_pattern(label, example)) is not None
        )
        x = slot.get("x") or {}
        y = slot.get("y") or {}
        observed_count = _safe_int(x.get("n")) or len(examples)
        stable_geometry = _slot_spread(x) <= 160 and _slot_spread(y) <= 80
        stable_format = len(patterns) == 1
        safe_to_mutate = bool(
            observed_count >= 2
            and patterns
            and stable_format
            and stable_geometry
        )
        top_pattern = patterns.most_common(1)[0][0] if patterns else None
        fields[label] = {
            "label": label,
            "safe_to_mutate": safe_to_mutate,
            "observed_count": observed_count,
            "examples": examples[:5],
            "format_counts": dict(sorted(patterns.items())),
            "stable_format": top_pattern if stable_format else None,
            "stable_geometry": stable_geometry,
            "mutation_strategy": (
                f"replace {label.lower()} text in-place using observed format and bbox"
                if safe_to_mutate
                else None
            ),
            "blockers": _mutable_field_blockers(
                observed_count=observed_count,
                patterns=patterns,
                stable_format=stable_format,
                stable_geometry=stable_geometry,
            ),
        }
    for label in _SCRUBBABLE_LABELS:
        slot = label_slots.get(label)
        if not isinstance(slot, dict):
            continue
        examples = [str(value) for value in slot.get("examples") or []]
        scrubbable = [
            example for example in examples if _value_scrub_kind(label, example)
        ]
        kinds = Counter(
            kind
            for example in scrubbable
            if (kind := _value_scrub_kind(label, example)) is not None
        )
        safe_to_mutate = bool(scrubbable)
        fields[label] = {
            "label": label,
            "safe_to_mutate": safe_to_mutate,
            "mutation_kind": "value_scrub" if safe_to_mutate else None,
            "observed_count": len(scrubbable),
            "examples": scrubbable[:5],
            "scrub_kind_counts": dict(sorted(kinds.items())),
            # Per-word in-place scrub reuses the original bbox, so geometry is
            # stable by construction regardless of the label's overall spread.
            "stable_geometry": bool(safe_to_mutate),
            "stable_format": "value_scrub" if safe_to_mutate else None,
            "mutation_strategy": (
                f"scrub {label.lower()} digits in-place (mask/length/box preserved)"
                if safe_to_mutate
                else None
            ),
            "blockers": (
                [] if safe_to_mutate else ["no_scrubbable_value_observed"]
            ),
        }
    return fields


def _datetime_pattern(label: str, text: str) -> str | None:
    value = text.strip().upper().replace(" ", "")
    if label == "DATE":
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", value):
            year = value.rsplit("/", 1)[-1]
            return "MM/DD/YYYY" if len(year) == 4 else "MM/DD/YY"
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value):
            return "YYYY-MM-DD"
        if re.fullmatch(r"\d{1,2}-\d{1,2}-\d{2,4}", value):
            year = value.rsplit("-", 1)[-1]
            return "MM-DD-YYYY" if len(year) == 4 else "MM-DD-YY"
    if label == "TIME":
        if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}(AM|PM)?", value):
            return (
                "HH:MM:SS AM/PM"
                if value.endswith(("AM", "PM"))
                else "HH:MM:SS"
            )
        if re.fullmatch(r"\d{1,2}:\d{2}(AM|PM)?", value):
            return "HH:MM AM/PM" if value.endswith(("AM", "PM")) else "HH:MM"
    return None


def _slot_spread(summary: dict[str, Any]) -> float:
    p10 = _safe_float(summary.get("p10"), 0.0)
    p90 = _safe_float(summary.get("p90"), 0.0)
    return abs(p90 - p10)


def _mutable_field_blockers(
    *,
    observed_count: int,
    patterns: Counter[str],
    stable_format: bool,
    stable_geometry: bool,
) -> list[str]:
    blockers: list[str] = []
    if observed_count < 2:
        blockers.append("needs_multiple_observed_values")
    if not patterns:
        blockers.append("unsupported_format")
    elif not stable_format:
        blockers.append("mixed_formats")
    if not stable_geometry:
        blockers.append("unstable_geometry")
    return blockers


def _summarize_categories(analyses: list[MerchantAnalysis]) -> dict[str, Any]:
    heading_counts = Counter(
        category
        for analysis in analyses
        for category in analysis.category_sequence
    )
    item_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for analysis in analyses:
        for item in analysis.line_items:
            item_counts[item.category][item.product_text] += 1
    return {
        "heading_counts": dict(heading_counts.most_common()),
        "top_items_by_category": {
            category: [
                {"product_text": text, "count": count}
                for text, count in counts.most_common(8)
            ]
            for category, counts in sorted(item_counts.items())
        },
    }


def _summarize_tax_policy(
    analyses: list[MerchantAnalysis], merchant_name: str = ""
) -> dict[str, Any]:
    taxable_item_count = sum(
        1
        for analysis in analyses
        for item in analysis.line_items
        if item.taxable
    )
    non_taxable_item_count = sum(
        1
        for analysis in analyses
        for item in analysis.line_items
        if not item.taxable
    )
    receipts_with_tax_total = sum(
        1
        for analysis in analyses
        if analysis.tax_total is not None
        and analysis.tax_total > Decimal("0.00")
    )
    receipts_with_taxable_items = sum(
        1
        for analysis in analyses
        if any(item.taxable for item in analysis.line_items)
    )
    rate_observations = _tax_rate_observations(analyses)
    stable_tax_rate = len(rate_observations) >= 2 and max(
        rate_observations
    ) - min(rate_observations) <= Decimal("0.0050")
    # Readiness is config-driven, NOT observation-driven: the per-merchant tax
    # config carries the rate validated from real receipts, so taxable edits do
    # not require this run to re-derive a clean rate (real per-item taxable-flag
    # OCR is too sparse — observed per-receipt rates are wildly inflated). The
    # observation fields above are still reported for transparency.
    #   * not validated for taxable edits at all -> blocked (keeps Costco/Home
    #     Depot off: per-item flag OCR untrustworthy); or
    #   * a MULTI-jurisdiction merchant (Target) where this run can't confirm a
    #     single jurisdiction rate (needs a clean per-receipt observation).
    blockers: list[str] = []
    if not merchant_supports_taxable_edits(merchant_name):
        blockers.append("merchant_not_validated_for_taxable_edits")
    elif not _merchant_taxable_edits_available(merchant_name, analyses):
        blockers.append("observed_rate_off_validated_jurisdiction")

    # The loader independently re-validates every taxable edit's tax math, so
    # this is the contract signal, not the guarantee.
    tax_changing_ready = not blockers

    result: dict[str, Any] = {
        "supported_policy": (
            "taxable_item_delta" if tax_changing_ready else "non_taxable_item_delta"
        ),
        "taxable_item_count": taxable_item_count,
        "non_taxable_item_count": non_taxable_item_count,
        "receipts_with_tax_total": receipts_with_tax_total,
        "receipts_with_taxable_items": receipts_with_taxable_items,
        "tax_rate_observation_count": len(rate_observations),
        "stable_tax_rate": stable_tax_rate,
        "tax_changing_synthesis_ready": tax_changing_ready,
        "tax_changing_synthesis_blockers": blockers,
    }
    if rate_observations:
        average_rate = sum(rate_observations, Decimal("0.0000")) / Decimal(
            len(rate_observations)
        )
        result.update(
            {
                "avg_tax_rate": _format_rate(average_rate),
                "min_tax_rate": _format_rate(min(rate_observations)),
                "max_tax_rate": _format_rate(max(rate_observations)),
                "avg_tax_rate_percent": _format_rate_percent(average_rate),
            }
        )
    return result


def _receipt_observed_taxable_rate(
    analysis: MerchantAnalysis,
) -> Decimal | None:
    """This receipt's own taxable-item rate (tax / sum of taxable line totals),
    or ``None`` when it has no positive TAX or no parsed taxable items."""
    if analysis.tax_total is None or analysis.tax_total <= Decimal("0.00"):
        return None
    taxable_subtotal = _money_sum(
        item.amount for item in analysis.line_items if item.taxable
    )
    if taxable_subtotal <= Decimal("0.00"):
        return None
    return (analysis.tax_total / taxable_subtotal).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _receipt_effective_tax_rate(
    analysis: MerchantAnalysis,
) -> Decimal | None:
    """This receipt's EFFECTIVE rate (tax / subtotal) from summary anchors.

    Uses the labeled SUBTOTAL, else ``grand_total - tax`` — both reliable, unlike
    the per-item taxable flags. Usually a lower bound on the taxable-item rate
    (the taxable base is typically a subset of the subtotal), which makes it a
    conservative one-sided jurisdiction check: effective clearly > validated_rate
    signals a higher-tax jurisdiction than the config store. It is NOT exact —
    coupons (tax on the pre-discount base, subtotal post-discount) or cent
    rounding on tiny receipts can push it above the real rate — so the caller
    treats a breach as a safe skip, never as proof of a specific rate.
    """
    if analysis.tax_total is None or analysis.tax_total <= Decimal("0.00"):
        return None
    subtotal = analysis.subtotal
    if subtotal is None and analysis.grand_total is not None:
        subtotal = _money(
            max(Decimal("0.00"), analysis.grand_total - analysis.tax_total)
        )
    if subtotal is None or subtotal <= Decimal("0.00"):
        return None
    return (analysis.tax_total / subtotal).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _tax_rate_observations(analyses: list[MerchantAnalysis]) -> list[Decimal]:
    return [
        rate
        for analysis in analyses
        if (rate := _receipt_observed_taxable_rate(analysis)) is not None
    ]


def _has_unobserved_positive_tax_receipt(
    analyses: list[MerchantAnalysis],
) -> bool:
    """True when some receipt has positive TAX but no parsed taxable items.

    Such a receipt is INVISIBLE to the rate-observation set (its jurisdiction
    can't be confirmed), so for a multi-jurisdiction merchant it could secretly
    belong to a different jurisdiction than the unanimous batch rate.
    """
    return any(
        analysis.tax_total is not None
        and analysis.tax_total > Decimal("0.00")
        and _receipt_observed_taxable_rate(analysis) is None
        for analysis in analyses
    )


def _consistent_validated_edit_rate(
    merchant_name: str, analyses: list[MerchantAnalysis]
) -> Decimal | None:
    """Single receipt-validated rate to apply to a taxable edit, or ``None``.

    Validates PER RECEIPT, not on the batch median: every receipt's own
    observed taxable-item rate must snap to the SAME validated jurisdiction
    rate. A batch median can hide a minority of off-jurisdiction receipts (an
    imbalanced Target mix ``[9.50, 9.50, 9.75]`` medians to 9.50 yet contains a
    9.75 receipt) — requiring unanimity rejects any mixed-jurisdiction batch so
    a recomputed TAX is never applied at the wrong jurisdiction's rate. Needs at
    least two agreeing observations so a single noisy receipt cannot unlock it.
    """
    observations = _tax_rate_observations(analyses)
    if len(observations) < 2:
        return None
    snapped = {
        merchant_taxable_edit_rate(merchant_name, rate) for rate in observations
    }
    if len(snapped) != 1 or None in snapped:
        return None
    return next(iter(snapped))


def _merchant_taxable_edits_available(
    merchant_name: str, analyses: list[MerchantAnalysis]
) -> bool:
    """Merchant-level capability: can ANY taxable edit be produced this run?

    A single-jurisdiction validated merchant always can (the config rate
    applies). A multi-jurisdiction merchant can only when this run confirms a
    single jurisdiction rate. Mirrors ``_taxable_edit_rate_for_receipt`` so the
    readiness report never claims an edit the generators would reject.
    """
    profile = merchant_tax_profile(merchant_name)
    if profile is None or not profile.can_support_taxable_edits:
        return False
    if len(profile.allowed_rates()) == 1:
        return True
    return (
        not _has_unobserved_positive_tax_receipt(analyses)
        and _consistent_validated_edit_rate(merchant_name, analyses) is not None
    )


def _taxable_edit_rate_for_receipt(
    merchant_name: str,
    analyses: list[MerchantAnalysis],
    analysis: MerchantAnalysis,
) -> Decimal | None:
    """Validated rate to recompute TAX for a taxable edit ON ``analysis``.

    The merchant's tax config carries the rate(s) validated by reading its real
    receipts — that is the ground truth. We do NOT re-derive the rate from this
    run's per-item taxable detection, which is far too sparse to trust (real
    receipts flag only a fraction of taxable items, so ``tax / detected_taxable
    _subtotal`` is wildly inflated — e.g. an apparent 147%). Two cases:

      * SINGLE-jurisdiction merchant (one validated rate): apply that rate
        directly. There is only one possible rate, so no per-receipt observation
        is needed to disambiguate, and the recomputed TAX uses the verified
        rate. (A taxable add/remove changes TAX by ``delta * rate``; the
        receipt's own — possibly broken — taxable detection does not enter the
        delta math.)
      * MULTI-jurisdiction merchant (Target: NV vs CA): the rate depends on the
        store, so the target receipt must itself observe a rate that snaps to a
        single validated jurisdiction rate, and the batch must contain no
        unobserved positive-TAX receipt whose jurisdiction is unknown (a blind
        off-jurisdiction receipt could otherwise be edited at the wrong rate).
        When real per-item OCR is too sparse to observe a clean per-receipt
        rate, this correctly yields no taxable edits rather than guessing NV vs
        CA.
    """
    profile = merchant_tax_profile(merchant_name)
    if profile is None or not profile.can_support_taxable_edits:
        return None
    allowed = profile.allowed_rates()
    if len(allowed) == 1:
        config_rate = allowed[0]
        # Profiles are brand-matched, so a same-brand receipt from a HIGHER-tax
        # jurisdiction (or a later rate period) could otherwise be edited at the
        # stale config rate. Usually the taxable base is a subset of the subtotal,
        # so the effective rate (tax / subtotal, from reliable summary anchors —
        # not the sparse per-item flags) sits at or below the taxable-item rate;
        # an effective rate clearly above the config rate then signals a
        # different, higher jurisdiction, and we refuse. (All validated
        # single-rate merchants are CA at the 7.25% state minimum, so there is no
        # lower-jurisdiction direction to miss.) This is a CONSERVATIVE one-sided
        # guard, not exact: a legitimate same-jurisdiction receipt can also breach
        # it when the printed subtotal is post-discount but tax is on the
        # pre-discount base (coupons), or via cent-rounding on a tiny receipt.
        # Those are accepted false-skips — we drop a candidate rather than ever
        # emit a wrong-jurisdiction tax, which is the safe direction.
        effective = _receipt_effective_tax_rate(analysis)
        if effective is not None and effective > config_rate + Decimal("0.005"):
            return None
        return config_rate
    # Multi-jurisdiction: need per-receipt jurisdiction confirmation.
    if _has_unobserved_positive_tax_receipt(analyses):
        return None
    own_rate = _receipt_observed_taxable_rate(analysis)
    if own_rate is None:
        return None
    snapped = merchant_taxable_edit_rate(merchant_name, own_rate)
    if snapped is None:
        return None
    # Cross-check the batch agrees on this one jurisdiction so a lone receipt
    # cannot unlock edits for a mixed batch.
    if _consistent_validated_edit_rate(merchant_name, analyses) != snapped:
        return None
    return snapped


def _format_rate(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):.4f}"


def _format_rate_percent(value: Decimal) -> str:
    percent = value * Decimal("100")
    return f"{percent.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}%"


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "merchant_name": profile["merchant_name"],
        "receipt_count": profile["receipt_count"],
        "generation_limits": profile["generation_limits"],
        "category_patterns": profile["category_patterns"],
        "tax_policy": profile.get("tax_policy") or {},
        "real_structure_baseline": profile.get("real_structure_baseline")
        or {},
    }


def _match_total_row(
    product_row: dict[str, Any],
    total_rows: list[dict[str, Any]],
    used_total_lines: set[int],
) -> dict[str, Any] | None:
    same_line = [
        row
        for row in total_rows
        if row["line_index"] == product_row["line_index"]
        and row["line_index"] not in used_total_lines
    ]
    if same_line:
        return same_line[0]
    nearby = [
        row
        for row in total_rows
        if row["line_index"] not in used_total_lines
        and abs(row["center_y"] - product_row["center_y"]) <= 16
    ]
    if not nearby:
        return None
    return min(
        nearby,
        key=lambda row: (
            abs(row["center_y"] - product_row["center_y"]),
            abs(row["line_index"] - product_row["line_index"]),
        ),
    )


def _category_insert_y(
    analysis: MerchantAnalysis,
    category: str,
) -> float | None:
    items = [item for item in analysis.line_items if item.category == category]
    if not items:
        return None
    # Insert into the GAP below the lowest item of the category — below its
    # full band's bottom edge — never at an item's center (which, for a two-row
    # item, sits between its name and price rows and would collide with them).
    lowest = min(
        items, key=lambda item: item.band_bottom_y or item.center_y
    )
    gap = max(
        12,
        _line_step(
            analysis.line_items,
            analysis.receipt,
            allow_font_geometry_fallback=True,
        )
        // 2,
    )
    bottom = lowest.band_bottom_y or lowest.center_y
    return max(24.0, bottom - gap)


def _category_insertion_context(
    analysis: MerchantAnalysis,
    category: str,
    y_center: float,
) -> dict[str, Any]:
    items = [item for item in analysis.line_items if item.category == category]
    category_item_ys = [item.center_y for item in items]
    nearest_category_item_y = (
        min(category_item_ys, key=lambda item_y: abs(item_y - y_center))
        if category_item_ys
        else None
    )
    lower_line_ys = [
        _line_y(line) * 1000
        for line in analysis.receipt.get("lines", [])
        if _line_y(line) * 1000 < y_center
    ]
    nearest_lower_line_y = max(lower_line_ys, default=None)
    same_category_section = bool(
        items
        and nearest_category_item_y is not None
        and y_center < nearest_category_item_y
        and (nearest_lower_line_y is None or y_center > nearest_lower_line_y)
    )
    selection_reason = (
        "observed item from another receipt inserted under the same category "
        "on the base receipt"
        if same_category_section
        else "observed item from another receipt inserted with merchant-local "
        "price-column geometry"
    )
    return {
        "category_item_count_before": len(items) if items else None,
        "nearest_category_item_y": (
            round(float(nearest_category_item_y), 1)
            if nearest_category_item_y is not None
            else None
        ),
        "nearest_lower_line_y": (
            round(float(nearest_lower_line_y), 1)
            if nearest_lower_line_y is not None
            else None
        ),
        "same_category_section": same_category_section,
        "selection_reason": selection_reason,
    }


def _merchant_item_tax_flag(
    merchant_name: str | None, taxable: bool
) -> str | None:
    """The single trailing tax-class token this merchant prints for a taxable
    (or non-taxable) item, or ``None`` when there is no validated flag for that
    class. ``sorted(...)[0]`` makes the canonical pick stable for the rare
    multi-flag set so synthesis is deterministic.
    """
    if not merchant_name:
        return None
    profile = merchant_tax_profile(merchant_name)
    if profile is None:
        return None
    flags = profile.taxable_flags if taxable else profile.nontaxable_flags
    if not flags:
        return None
    return sorted(flags)[0]


def _truncate_token_to_width(token: str, max_width: int) -> str:
    """Longest prefix of ``token`` (with a trailing ``...`` when shortened) whose
    rendered width fits in ``max_width`` px, or ``""`` when even the shortest
    truncated form will not fit.
    """
    if _token_width(token) <= max_width:
        return token
    for keep in range(len(token) - 1, 0, -1):
        candidate = token[:keep] + "..."
        if _token_width(candidate) <= max_width:
            return candidate
    return ""


def _truncate_tokens_to_width(
    tokens: list[str], max_width: int
) -> list[str]:
    """Keep the leading product tokens that fit within ``max_width`` px (same
    per-token width + 10px spacing model as ``_build_line``); truncate the first
    overflowing token with a trailing ``...``. Always returns at least one
    (possibly truncated) token so the row keeps a PRODUCT_NAME.
    """
    if not tokens:
        return tokens
    kept: list[str] = []
    used = 0
    for token in tokens:
        spacing = 10 if kept else 0
        width = _token_width(token)
        if used + spacing + width <= max_width:
            kept.append(token)
            used += spacing + width
            continue
        if not kept:
            # First token alone overflows the lane: truncate it (and keep the
            # full token only if even an ellipsis form will not fit).
            kept.append(
                _truncate_token_to_width(token, max_width) or token
            )
        break
    return kept


def _build_line_item_line(
    receipt: dict[str, Any],
    entry: MerchantCatalogEntry,
    *,
    y_center: float,
    merchant_name: str | None = None,
    marker_token: str | None = None,
) -> dict[str, Any]:
    # ``_label_x_p50`` yields 0.0 (not None) for an unmeasured label, so treat a
    # non-positive value as missing and fall back to the column defaults.
    product_x_raw = _label_x_p50(receipt, "PRODUCT_NAME")
    product_x = product_x_raw if product_x_raw and product_x_raw > 0 else 90
    product_x = max(25, int(round(product_x - 35)))
    price = _format_money(entry.amount)
    price_width = _token_width(price)
    # RIGHT-anchor the price to the LINE_TOTAL column's real right edge so every
    # generated item price ends at the SAME x (one hard right column). Fall back
    # to the center column (+ half a price width) then a default right edge when
    # the scaffold has no measurable LINE_TOTAL right edge.
    right_x_raw = _label_right_x_p50(receipt, "LINE_TOTAL")
    if right_x_raw and right_x_raw > 0:
        right_x = right_x_raw
    else:
        center_raw = _label_x_p50(receipt, "LINE_TOTAL")
        center = center_raw if center_raw and center_raw > 0 else 850
        right_x = center + price_width / 2
    right_x = int(round(min(996, right_x)))
    # The price's left edge (right_x - width) is also the budget against which the
    # product name is truncated, so name and price never overlap.
    price_x = max(0, min(1000 - price_width, right_x - price_width))
    flag = _merchant_item_tax_flag(merchant_name, entry.taxable)
    # Reserve room for the markdown marker ("NOW") that prints just before the
    # price on a discounted row, so truncating the name never collides with it.
    marker_reserve = (_token_width(marker_token) + 10) if marker_token else 0
    # Truncate the product name so it ends cleanly before the price column
    # (leaving a 10px gutter + any marker room), regardless of a trailing flag.
    name_budget = max(0, price_x - product_x - 10 - marker_reserve)
    name_tokens = _truncate_tokens_to_width(entry.product_tokens, name_budget)
    tokens = list(name_tokens)
    labels = ["PRODUCT_NAME"] * len(name_tokens)
    if marker_token:
        # The sale marker sits just left of the price, supervision-neutral (O).
        tokens.append(marker_token)
        labels.append("O")
    tokens.append(price)
    labels.append("LINE_TOTAL")
    price_index = len(tokens) - 1
    if flag:
        tokens.append(flag)
        labels.append("O")
    return _build_line(
        tokens,
        labels,
        x0=product_x,
        y0=max(0, min(976, int(round(y_center - 12)))),
        price_right_x=right_x,
        price_index=price_index,
    )


def _fill_sale_sub_line_text(template, price: Decimal) -> str | None:
    """Render a merchant's sale sub-line template (``SALE {n} {price}, WAS:
    {price} each``) with concrete values: quantity 1, the row's own price as the
    sale price, and a strictly larger "WAS" price (a discount only reads as a
    discount when WAS > NOW)."""
    if template is None or not template.sub_line.present:
        return None
    raw = template.sub_line.template
    if not raw:
        return None
    sale = _money(price)
    was = _money(price * Decimal("1.5"))
    if was <= sale:
        was = _money(sale + Decimal("1.00"))
    # The grammar's {price} placeholder consumed the dollar sign, so re-add it.
    prices = iter([f"${_format_money(sale)}", f"${_format_money(was)}"])
    filled_prices = re.sub(
        r"\{price\}", lambda _m: next(prices, f"${_format_money(sale)}"), raw
    )
    text = filled_prices.replace("{n}", "1")
    return re.sub(r"\s+", " ", text).strip() or None


def _build_sale_sub_line(
    *,
    template,
    price: Decimal,
    line_id: int,
    y0: int,
    char_w: int,
    height: int,
    x0: int,
) -> dict[str, Any] | None:
    """A SECOND synthetic line beneath an item row rendering the merchant's sale
    sub-line (``SALE 1 $x.xx, WAS: $y.yy each``).

    Every token is supervision-neutral (all ``O``): the sub-line is decorative
    real-format copy, not item supervision. Returns ``None`` when the merchant has
    no sub-line grammar or nothing fits on canvas."""
    text = _fill_sale_sub_line_text(template, price)
    if not text:
        return None
    tokens = text.split()
    if not tokens:
        return None
    char_w = max(1, int(char_w))
    # TIGHT single word-space between every token (NOT a full character cell). A
    # full ``char_w`` gap between every token was the #1 realism tell: the SALE
    # sub-line read as a "floating", pseudo-justified row instead of natural copy.
    # Real receipts set ~1/3 of a cell between words, so we emit the verifier-safe
    # ``_template_word_gap`` here. The fixed-pitch grid renderer keeps adjacent
    # LEFT-aligned tokens from gluing on its own (``draw_grid_line`` advances a
    # per-row cursor and nudges the next token to at least one cell past the prior
    # word), and after a right-anchored price token the renderer's cursor advance
    # separates the following flag/unit ("$7.49 each") -- so a tight SOURCE gap no
    # longer needs to be bloated to a full cell to render with visible spaces.
    gap = _template_word_gap(height)
    widths = [max(1, len(token)) * char_w for token in tokens]
    total = sum(widths) + gap * (len(tokens) - 1)
    x0 = int(x0)
    # Keep the whole sub-line on canvas so the WAS value and the trailing "each"
    # never run off the right edge and read as truncated.
    if x0 + total > 1000:
        x0 = max(0, 1000 - total)
    if total > 1000:  # pathological width: shrink the cell so nothing is dropped
        scale = 1000.0 / total
        char_w = max(1, int(char_w * scale))
        gap = max(1, int(gap * scale))
        widths = [max(1, len(token)) * char_w for token in tokens]
        x0 = 0
    words: list[dict[str, Any]] = []
    cursor = x0
    for token, width in zip(tokens, widths):
        x1 = min(1000, cursor + width)
        words.append(
            {
                "text": token,
                "bbox": [cursor, y0, x1, y0 + height],
                "labels": [],
                "line_id": line_id,
                "word_id": len(words) + 1,
            }
        )
        cursor = min(1000, x1 + gap)
    if not words:
        return None
    return {"line_id": line_id, "y": y0 / 1000, "words": words}


def _build_line_item_band(
    receipt: dict[str, Any],
    entry: MerchantCatalogEntry,
    *,
    y_center: float,
    merchant_name: str | None,
    template,
    line_step: int,
) -> list[dict[str, Any]]:
    """A synthetic fallback item band: ``[primary_line]`` or ``[primary_line,
    sub_line]``.

    The primary row carries the markdown marker (when the merchant's grammar
    prints one); when the grammar also prints a sale sub-line, a second all-``O``
    line is placed one ``line_step`` below the primary (the add path then tilts +
    anchors the whole band as a unit)."""
    marker_token = (
        template.markdown_marker.token
        if (template is not None and template.markdown_marker.present)
        else None
    )
    primary = _build_line_item_line(
        receipt,
        entry,
        y_center=y_center,
        merchant_name=merchant_name,
        marker_token=marker_token,
    )
    band = [primary]
    if template is not None and template.sub_line.present:
        # Coordinate model is y-high-is-top, so a row one step BELOW the primary
        # sits one ``line_step`` lower in y. Indent the sub-line to the primary's
        # name column.
        name_x0 = min(
            (w["bbox"][0] for w in primary.get("words") or []), default=90
        )
        # Size the sub-line's character cell to the receipt's real font pitch so
        # its one-cell word gaps match what the fixed-pitch renderer snaps to (a
        # too-small cell would let the grid collapse the gaps and glue tokens).
        sub = _build_sale_sub_line(
            template=template,
            price=entry.amount,
            line_id=_SYNTHETIC_LINE_ID_BASE + 1,
            y0=max(0, int(round(y_center - line_step - 12))),
            char_w=_render_cell_width(receipt),
            height=24,  # matches _build_line's primary row height
            x0=int(name_x0),
        )
        if sub is not None:
            band.append(sub)
    return band


def _observed_item_evidence(
    entry: MerchantCatalogEntry,
    base_analysis: MerchantAnalysis,
    analyses: list[MerchantAnalysis],
) -> dict[str, Any]:
    base_key = _receipt_key(base_analysis.receipt)
    product_sources = sorted(entry.source_receipt_keys)
    category_sources = sorted(
        {
            _receipt_key(analysis.receipt)
            for analysis in analyses
            if any(
                item.category == entry.category for item in analysis.line_items
            )
        }
    )
    category_heading_sources = sorted(
        {
            _receipt_key(analysis.receipt)
            for analysis in analyses
            if entry.category in analysis.category_sequence
        }
    )
    return {
        "base_receipt_key": base_key,
        "product_seen_in_receipts": product_sources[:8],
        "product_seen_outside_base": [
            source for source in product_sources if source != base_key
        ][:8],
        "product_observed_count": entry.count,
        "category": entry.category,
        "category_seen_in_receipts": category_sources[:8],
        "category_seen_count": len(category_sources),
        "category_heading_seen_count": len(category_heading_sources),
        "base_receipt_has_category": base_key in category_sources,
    }


def _build_line(
    tokens: list[str],
    labels: list[str],
    *,
    x0: int,
    y0: int,
    price_x: int | None = None,
    price_index: int | None = None,
    price_right_x: int | None = None,
) -> dict[str, Any]:
    # ``price_index`` is the 0-based index of the price token that must snap to
    # the merchant's price column. It defaults to the final token so existing
    # callers (price is last) are unchanged; when a trailing tax flag follows the
    # price, the caller passes the price's own index so the price still lands in
    # its column and the flag flows just after it.
    #
    # ``price_right_x`` RIGHT-anchors the price token (``bbox[2] == price_right_x``,
    # i.e. ``x0 = price_right_x - width``) so every generated item price ends at
    # the SAME right-edge column. ``price_x`` LEFT-anchors it (legacy) and is used
    # only when ``price_right_x`` is not supplied.
    if (price_x is not None or price_right_x is not None) and price_index is None:
        price_index = len(tokens) - 1
    words = []
    cursor = x0
    for idx, token in enumerate(tokens):
        label = labels[idx] if idx < len(labels) else "O"
        width = _token_width(token)
        if idx == price_index and price_right_x is not None:
            cursor = max(0, price_right_x - width)
        elif idx == price_index and price_x is not None:
            cursor = price_x
        words.append(
            {
                "text": token,
                "bbox": [cursor, y0, min(1000, cursor + width), y0 + 24],
                "labels": [] if label == "O" else [label],
                "line_id": 20_000,
                "word_id": idx + 1,
            }
        )
        cursor = min(1000, cursor + width + 10)
    return {"line_id": 20_000, "y": y0 / 1000, "words": words}


def _insert_line_sorted(receipt: dict[str, Any], line: dict[str, Any]) -> None:
    lines = receipt.setdefault("lines", [])
    inserted_y = _line_y(line)
    insert_at = len(lines)
    for idx, existing in enumerate(lines):
        if _line_y(existing) < inserted_y:
            insert_at = idx
            break
    lines.insert(insert_at, line)
    _refresh_words(receipt)


def _shift_lines_below_for_insert(
    receipt: dict[str, Any],
    *,
    inserted_center_y: float,
    delta: int,
) -> dict[str, int]:
    realized_shifts: list[int] = []
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 >= inserted_center_y:
            continue
        before_y = _line_y(line) * 1000
        for word in line.get("words", []):
            word["bbox"][1] = max(0, word["bbox"][1] - delta)
            word["bbox"][3] = max(0, word["bbox"][3] - delta)
        line["y"] = max(0.0, _line_y(line))
        realized_shift = int(round(before_y - (_line_y(line) * 1000)))
        if realized_shift > 0:
            realized_shifts.append(realized_shift)
    _refresh_words(receipt)
    if not realized_shifts:
        return {
            "line_count": 0,
            "median_shift": 0,
            "min_shift": 0,
            "max_shift": 0,
        }
    return {
        "line_count": len(realized_shifts),
        "median_shift": int(round(statistics.median(realized_shifts))),
        "min_shift": min(realized_shifts),
        "max_shift": max(realized_shifts),
    }


def _apply_non_taxable_delta(
    receipt: dict[str, Any],
    analysis: MerchantAnalysis,
    *,
    delta: Decimal,
) -> dict[str, Any]:
    old_subtotal = analysis.subtotal
    if old_subtotal is None:
        old_subtotal = _money_sum(item.amount for item in analysis.line_items)
    old_grand_total = analysis.grand_total
    if old_grand_total is None:
        old_grand_total = old_subtotal

    new_subtotal = _money(max(Decimal("0.00"), old_subtotal + delta))
    new_grand_total = _money(max(Decimal("0.00"), old_grand_total + delta))
    updated = {
        "subtotal": 0,
        "grand_total": 0,
        "payment_or_balance": 0,
    }

    for line in receipt.get("lines", []):
        for word in line.get("words", []):
            labels = set(word.get("labels") or [])
            value = _parse_money(word.get("text"))
            if value is None:
                continue
            if "SUBTOTAL" in labels:
                word["text"] = _format_money_like(word["text"], new_subtotal)
                _right_align_money_box(word)
                updated["subtotal"] += 1
            elif "GRAND_TOTAL" in labels:
                word["text"] = _format_money_like(
                    word["text"], new_grand_total
                )
                _right_align_money_box(word)
                updated["grand_total"] += 1
            elif value == old_grand_total and not labels & {
                "LINE_TOTAL",
                "TAX",
                "SUBTOTAL",
                "GRAND_TOTAL",
            }:
                word["text"] = _format_money_like(
                    word["text"], new_grand_total
                )
                _right_align_money_box(word)
                updated["payment_or_balance"] += 1

    _refresh_words(receipt)
    return {
        "summary_update_policy": "non_taxable_item_delta",
        "old_subtotal": _format_money(old_subtotal),
        "new_subtotal": _format_money(new_subtotal),
        "old_grand_total": _format_money(old_grand_total),
        "new_grand_total": _format_money(new_grand_total),
        "subtotal_delta": _format_money(delta),
        "grand_total_delta": _format_money(delta),
        "tax_delta": "0.00",
        "tax_policy": "left unchanged because synthesized item is non-taxable",
        "updated_summary_labels": updated,
    }


def _apply_taxable_delta(
    receipt: dict[str, Any],
    analysis: MerchantAnalysis,
    *,
    delta: Decimal,
    rate: Decimal,
) -> dict[str, Any] | None:
    """Reconcile a TAXABLE item add/remove using the merchant's stable taxable-item
    tax rate: subtotal moves by the item price, tax moves by ``delta * rate``
    (rounded to cents), and the grand total absorbs both. Unlike the non-taxable
    path (which freezes tax), this edits the real TAX value — only safe because a
    validated stable rate is the merchant's tax model.

    Returns the arithmetic evidence, or None when the receipt has no usable
    subtotal/tax anchors to reconcile against (so we never emit an unbalanced
    taxable edit).
    """
    old_tax = analysis.tax_total
    if old_tax is None or old_tax <= Decimal("0.00"):
        return None  # taxable math needs a real TAX anchor to move
    old_grand_total = analysis.grand_total
    old_subtotal = analysis.subtotal
    if old_subtotal is None:
        # Many real receipts label TAX + GRAND_TOTAL but omit a SUBTOTAL line
        # (e.g. the Vons/Sprouts exports carry zero SUBTOTAL words). Reconstruct
        # the taxable-math subtotal anchor from grand_total - tax — exact when
        # both are labeled — else fall back to the line-item sum. The loader
        # re-validates the emitted receipt's arithmetic, so a reconstructed
        # anchor that doesn't reconcile is dropped there, not trained on.
        if old_grand_total is not None:
            old_subtotal = _money(max(Decimal("0.00"), old_grand_total - old_tax))
        else:
            old_subtotal = _money_sum(
                item.amount for item in analysis.line_items
            )
    if old_subtotal <= Decimal("0.00"):
        return None  # no usable subtotal anchor to reconcile against
    if old_grand_total is None:
        old_grand_total = _money(old_subtotal + old_tax)

    tax_delta = _money(delta * rate)
    # A removal larger than the (possibly reconstructed) subtotal is not
    # reconcilable — bail rather than clamp to zero, which would emit a
    # self-consistent receipt whose subtotal movement != the item price.
    raw_new_subtotal = old_subtotal + delta
    if raw_new_subtotal < Decimal("0.00"):
        return None
    new_subtotal = _money(raw_new_subtotal)
    # Removing more tax than the receipt carries is not reconcilable — bail
    # rather than clamp tax to 0 (which would leave tax_delta inconsistent with
    # the actual tax movement).
    raw_new_tax = old_tax + tax_delta
    if raw_new_tax < Decimal("0.00"):
        return None
    new_tax = _money(raw_new_tax)
    new_grand_total = _money(new_subtotal + new_tax)

    updated = {"subtotal": 0, "tax": 0, "grand_total": 0, "payment_or_balance": 0}
    tax_words: list[tuple[dict[str, Any], Decimal]] = []
    summary_labels = {"SUBTOTAL", "TAX", "GRAND_TOTAL"}
    for line in receipt.get("lines", []):
        line_labels = {
            label
            for word in line.get("words", []) or []
            for label in (word.get("labels") or [])
            if label in summary_labels
        }
        for word in line.get("words", []):
            labels = set(word.get("labels") or [])
            value = _parse_money(word.get("text"))
            if value is None:
                continue
            effective_labels = set(labels)
            if not effective_labels & {"LINE_TOTAL", *summary_labels}:
                effective_labels |= line_labels
            if "SUBTOTAL" in effective_labels:
                word["text"] = _format_money_like(word["text"], new_subtotal)
                _right_align_money_box(word)
                updated["subtotal"] += 1
            elif "TAX" in effective_labels:
                tax_words.append((word, value))
            elif "GRAND_TOTAL" in effective_labels:
                word["text"] = _format_money_like(word["text"], new_grand_total)
                _right_align_money_box(word)
                updated["grand_total"] += 1
            elif value == old_grand_total and not labels & {
                "LINE_TOTAL",
                "TAX",
                "SUBTOTAL",
                "GRAND_TOTAL",
            }:
                word["text"] = _format_money_like(word["text"], new_grand_total)
                _right_align_money_box(word)
                updated["payment_or_balance"] += 1

    if tax_words:
        original_tax_total = _money_sum(value for _, value in tax_words)
        # Running-remainder distribution: clamp each share to [0, remaining] so
        # cent rounding can never produce a NEGATIVE tax row, and the last row
        # takes the exact remainder so the rows sum to new_tax precisely.
        remaining_tax = new_tax
        remaining_old = original_tax_total
        for index, (word, value) in enumerate(tax_words):
            if index == len(tax_words) - 1:
                share = remaining_tax
            elif remaining_old > Decimal("0.00"):
                share = _money(remaining_tax * value / remaining_old)
                share = min(max(share, Decimal("0.00")), remaining_tax)
            else:
                share = Decimal("0.00")
            remaining_tax -= share
            remaining_old -= value
            word["text"] = _format_money_like(word["text"], share)
            _right_align_money_box(word)
            updated["tax"] += 1

    _refresh_words(receipt)
    return {
        "summary_update_policy": "taxable_item_delta",
        "old_subtotal": _format_money(old_subtotal),
        "new_subtotal": _format_money(new_subtotal),
        "old_tax": _format_money(old_tax),
        "new_tax": _format_money(new_tax),
        "old_grand_total": _format_money(old_grand_total),
        "new_grand_total": _format_money(new_grand_total),
        "subtotal_delta": _format_money(delta),
        "tax_delta": _format_money(tax_delta),
        "grand_total_delta": _format_money(_money(delta + tax_delta)),
        "tax_rate": _format_rate(rate),
        "tax_policy": (
            "tax recomputed at the merchant's stable taxable-item rate "
            f"({_format_rate(rate)}) for a taxable item edit"
        ),
        "updated_summary_labels": updated,
    }


def _shift_lines_below(
    receipt: dict[str, Any],
    removed_center_y: float,
    delta: int,
) -> dict[str, int]:
    realized_shifts: list[int] = []
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 >= removed_center_y:
            continue
        before_y = _line_y(line) * 1000
        for word in line.get("words", []):
            word["bbox"][1] = min(1000, word["bbox"][1] + delta)
            word["bbox"][3] = min(1000, word["bbox"][3] + delta)
        line["y"] = min(1.0, _line_y(line))
        realized_shift = int(round((_line_y(line) * 1000) - before_y))
        if realized_shift > 0:
            realized_shifts.append(realized_shift)
    _refresh_words(receipt)
    if not realized_shifts:
        return {
            "line_count": 0,
            "median_shift": 0,
            "min_shift": 0,
            "max_shift": 0,
        }
    return {
        "line_count": len(realized_shifts),
        "median_shift": int(round(statistics.median(realized_shifts))),
        "min_shift": min(realized_shifts),
        "max_shift": max(realized_shifts),
    }


def _score_structure_similarity(
    candidate: MerchantAnalysis,
    real_analyses: list[MerchantAnalysis],
) -> dict[str, Any]:
    candidate_key = _receipt_key(candidate.receipt)
    pool = [
        analysis
        for analysis in real_analyses
        if _receipt_key(analysis.receipt) != candidate_key
    ] or real_analyses
    if not pool:
        return {
            "score": 0.0,
            "nearest_real_receipt_key": None,
            "components": {},
        }

    scored = [
        (_structure_components(candidate, analysis), analysis)
        for analysis in pool
    ]
    best_components, best = max(
        scored,
        key=lambda item: _weighted_structure_score(item[0]),
    )
    candidate_signature = _receipt_signature(candidate)
    nearest_signature = _receipt_signature(best)
    nearest_real_evidence = build_nearest_real_structure_evidence(
        best_components,
        candidate_signature,
        nearest_signature,
    )
    score = round(_weighted_structure_score(best_components), 3)
    baseline_comparison = compare_structure_to_real_baseline(
        score,
        build_real_structure_baseline(real_analyses),
    )
    return {
        "score": score,
        "nearest_real_receipt_key": _receipt_key(best.receipt),
        "components": {
            key: round(value, 3) for key, value in best_components.items()
        },
        "candidate_signature": candidate_signature,
        "nearest_signature": nearest_signature,
        "real_baseline_comparison": baseline_comparison,
        **nearest_real_evidence,
    }


def _structure_components(
    candidate: MerchantAnalysis,
    real: MerchantAnalysis,
) -> dict[str, float]:
    candidate_price_x = _label_x_p50(candidate.receipt, "LINE_TOTAL")
    real_price_x = _label_x_p50(real.receipt, "LINE_TOTAL")
    return {
        "category_sequence": _sequence_similarity(
            candidate.category_sequence,
            real.category_sequence,
        ),
        "category_set": _set_similarity(
            candidate.category_sequence,
            real.category_sequence,
        ),
        "item_count": _ratio_close(
            len(candidate.line_items), len(real.line_items)
        ),
        "token_count": _ratio_close(
            len(candidate.receipt.get("words", [])),
            len(real.receipt.get("words", [])),
        ),
        "price_column": (
            _distance_score(abs(candidate_price_x - real_price_x), scale=250)
            if candidate_price_x is not None and real_price_x is not None
            else 0.0
        ),
        "line_step": _distance_score(
            abs(
                _line_step(candidate.line_items, candidate.receipt)
                - _line_step(real.line_items, real.receipt)
            ),
            scale=40,
        ),
    }


def _weighted_structure_score(components: dict[str, float]) -> float:
    return (
        components.get("category_sequence", 0.0) * 0.25
        + components.get("category_set", 0.0) * 0.15
        + components.get("item_count", 0.0) * 0.18
        + components.get("token_count", 0.0) * 0.12
        + components.get("price_column", 0.0) * 0.18
        + components.get("line_step", 0.0) * 0.12
    )


def build_real_structure_baseline(
    analyses: list[MerchantAnalysis],
) -> dict[str, Any]:
    """Summarize normal real-to-real structure variation for one merchant."""
    valid = [analysis for analysis in analyses if analysis.line_items]
    component_values: dict[str, list[float]] = defaultdict(list)
    scores: list[float] = []

    for left_index, left in enumerate(valid):
        for right in valid[left_index + 1 :]:
            components = _structure_components(left, right)
            scores.append(
                _bounded_score(_weighted_structure_score(components))
            )
            for key, value in components.items():
                component_values[key].append(_bounded_score(value))

    return {
        "schema_version": "real-structure-baseline-v1",
        "receipt_count": len(valid),
        "pair_count": len(scores),
        "score_summary": _score_distribution_summary(scores),
        "component_summaries": {
            key: _score_distribution_summary(values)
            for key, values in sorted(component_values.items())
        },
    }


def compare_structure_to_real_baseline(
    structure_score: Any,
    baseline: Any,
) -> dict[str, Any] | None:
    """Compare one candidate structure score to real-to-real variation."""
    if not isinstance(baseline, dict):
        return None
    summary = baseline.get("score_summary")
    if not isinstance(summary, dict):
        return None
    pair_count = _safe_int(summary.get("count")) or _safe_int(
        baseline.get("pair_count")
    )
    if not pair_count:
        return None

    candidate_score = _maybe_float(structure_score)
    baseline_avg = _maybe_float(summary.get("avg"))
    baseline_min = _maybe_float(summary.get("min"))
    baseline_max = _maybe_float(summary.get("max"))
    if candidate_score is None or baseline_min is None:
        return None

    result: dict[str, Any] = {
        "baseline_receipt_count": _safe_int(baseline.get("receipt_count")),
        "baseline_pair_count": pair_count,
        "candidate_score": round(candidate_score, 3),
        "baseline_min": round(baseline_min, 3),
        "within_real_score_range": candidate_score >= baseline_min,
        "delta_from_min": round(candidate_score - baseline_min, 3),
    }
    if baseline_avg is not None:
        result["baseline_avg"] = round(baseline_avg, 3)
        result["delta_from_avg"] = round(candidate_score - baseline_avg, 3)
    if baseline_max is not None:
        result["baseline_max"] = round(baseline_max, 3)
    return {key: value for key, value in result.items() if value is not None}


def _score_distribution_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    bounded = [_bounded_score(value) for value in values]
    return {
        "count": len(bounded),
        "avg": round(statistics.mean(bounded), 3),
        "min": round(min(bounded), 3),
        "max": round(max(bounded), 3),
    }


def build_nearest_real_structure_evidence(
    components: dict[str, float],
    candidate_signature: dict[str, Any],
    nearest_signature: dict[str, Any],
) -> dict[str, Any]:
    """Explain how a synthetic receipt's shape compares with nearest real one."""
    rounded_components = {
        key: round(_bounded_score(value), 3)
        for key, value in sorted(components.items())
    }
    shape_deltas = _receipt_signature_deltas(
        candidate_signature,
        nearest_signature,
    )
    return {
        "shape_deltas": shape_deltas,
        "match_summary": _structure_match_summary(
            rounded_components,
            shape_deltas,
        ),
    }


def _receipt_signature_deltas(
    candidate_signature: dict[str, Any],
    nearest_signature: dict[str, Any],
) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for key in ("token_count", "line_count", "line_item_count"):
        candidate_value = _safe_int(candidate_signature.get(key))
        nearest_value = _safe_int(nearest_signature.get(key))
        if candidate_value is not None and nearest_value is not None:
            deltas[f"{key}_delta"] = candidate_value - nearest_value

    candidate_step = _safe_float(candidate_signature.get("line_step"), None)
    nearest_step = _safe_float(nearest_signature.get("line_step"), None)
    if candidate_step is not None and nearest_step is not None:
        deltas["line_step_delta"] = round(candidate_step - nearest_step, 3)

    candidate_price_x = _safe_float(
        candidate_signature.get("line_total_x_p50"),
        None,
    )
    nearest_price_x = _safe_float(
        nearest_signature.get("line_total_x_p50"),
        None,
    )
    if candidate_price_x is not None and nearest_price_x is not None:
        deltas["line_total_x_delta"] = round(
            candidate_price_x - nearest_price_x, 3
        )

    return deltas


def _structure_match_summary(
    components: dict[str, float],
    shape_deltas: dict[str, Any],
) -> dict[str, Any]:
    thresholds = {
        **SYNTHETIC_STRUCTURE_COMPONENT_THRESHOLDS,
        "item_count": 0.50,
    }
    matched_components = [
        component
        for component, value in components.items()
        if value >= thresholds.get(component, 0.75)
    ]
    weak_components = [
        component
        for component, value in components.items()
        if value < thresholds.get(component, 0.75)
    ]
    shape_checks: list[str] = []
    if abs(_safe_int(shape_deltas.get("line_count_delta")) or 0) <= 1:
        shape_checks.append("line_count_close")
    if abs(_safe_int(shape_deltas.get("line_item_count_delta")) or 0) <= 1:
        shape_checks.append("line_item_count_close")
    if components.get("token_count", 0.0) >= thresholds["token_count"]:
        shape_checks.append("token_count_close")
    if components.get("price_column", 0.0) >= thresholds["price_column"]:
        shape_checks.append("price_column_aligned")
    if components.get("line_step", 0.0) >= thresholds["line_step"]:
        shape_checks.append("line_spacing_close")
    if (
        components.get("category_sequence", 0.0)
        >= thresholds["category_sequence"]
    ):
        shape_checks.append("category_order_close")
    if components.get("category_set", 0.0) >= thresholds["category_set"]:
        shape_checks.append("category_set_close")

    return {
        "matched_components": matched_components,
        "weak_components": weak_components,
        "shape_checks": shape_checks,
    }


def _receipt_signature(analysis: MerchantAnalysis) -> dict[str, Any]:
    return {
        "token_count": len(analysis.receipt.get("words", [])),
        "line_count": len(analysis.receipt.get("lines", [])),
        "line_item_count": len(analysis.line_items),
        "category_sequence": analysis.category_sequence,
        "line_step": _line_step(analysis.line_items, analysis.receipt),
        "line_total_x_p50": _label_x_p50(analysis.receipt, "LINE_TOTAL"),
    }


def _flatten_lines(
    lines: list[dict[str, Any]],
    merchant_name: str | None = None,
) -> tuple[list[str], list[list[int]], list[str]]:
    tokens: list[str] = []
    bboxes: list[list[int]] = []
    tags: list[str] = []
    for line in lines:
        line_labels = []
        for word in line.get("words", []):
            tokens.append(word["text"])
            bboxes.append(word["bbox"])
            line_labels.append(_first_label(word.get("labels", [])))
        tags.extend(_bio_tags(line_labels))
    # Reconcile the flattened candidate so it survives objective verification.
    # Shared passes (see synthesis_reconcile.reconcile_candidate) run in canonical
    # order: dedupe_header -> dedupe_payment -> deoverlap+respace -> sanitize.
    tokens, bboxes, tags = reconcile_candidate(
        tokens, bboxes, tags, merchant_name=merchant_name
    )
    # Restore the canonical item-line grammar copy ("now" marker, "SALE"
    # sub-line) that the reconcile text-clean pass mis-"repairs" (now->how,
    # SALE->SALES) -- these are clean synthetic phrases, not OCR to scrub.
    tokens = _restore_canonical_grammar_tokens(tokens, tags)
    return tokens, bboxes, tags


def _hard_negative_tokens(label: str) -> list[str]:
    if label == "MERCHANT_NAME":
        return ["REWARDS", "CLUB"]
    if label == "ADDRESS_LINE":
        return ["LOCAL", "FAVORITES"]
    if label in AMOUNT_LABELS:
        return ["CHANGE", "0.00"]
    if label in {"DATE", "TIME", "STORE_HOURS"}:
        return ["SURVEY", "CODE"]
    return ["REF", "INFO"]


def _nearest_open_y(
    receipt: dict[str, Any],
    x0: int,
    desired_y: int,
    tokens: list[str],
) -> int | None:
    """Find a vertical gap near ``desired_y`` for a distractor line.

    A hard-negative distractor must sit in (or close to) its target zone, so the
    search stays local. Returns ``None`` when every local offset collides with
    existing words — a crowded receipt where the distractor cannot be placed
    cleanly. The caller skips such candidates rather than overlapping real words
    (degenerate geometry that the layout-integrity gate would reject anyway).
    """
    step = 18
    for distance in range(0, 144 + step, step):
        deltas = (0,) if distance == 0 else (-distance, distance)
        for delta in deltas:
            y0 = desired_y + delta
            if 0 <= y0 <= 976 and not _line_collides(
                receipt, x0, y0, tokens
            ):
                return y0
    return None


def _line_collides(
    receipt: dict[str, Any],
    x0: int,
    y0: int,
    tokens: list[str],
) -> bool:
    proposed = []
    cursor = x0
    for token in tokens:
        width = _token_width(token)
        proposed.append([cursor, y0, min(1000, cursor + width), y0 + 24])
        cursor = min(1000, cursor + width + 10)
    existing = [
        word["bbox"]
        for line in receipt.get("lines", [])
        for word in line.get("words", [])
    ]
    return any(
        _boxes_overlap(candidate, box, padding=3)
        for candidate in proposed
        for box in existing
    )


def _boxes_overlap(a: list[int], b: list[int], *, padding: int = 0) -> bool:
    return not (
        a[2] + padding <= b[0]
        or a[0] - padding >= b[2]
        or a[3] + padding <= b[1]
        or a[1] - padding >= b[3]
    )


def _boxes_have_significant_overlap(
    a: list[int],
    b: list[int],
    *,
    min_area_ratio: float = 0.35,
) -> bool:
    if not _boxes_overlap(a, b):
        return False
    x_overlap = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    y_overlap = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    overlap_area = x_overlap * y_overlap
    if overlap_area <= 0:
        return False
    left_area = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    right_area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return overlap_area / min(left_area, right_area) >= min_area_ratio


def _valid_layout_box(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if any(isinstance(item, bool) for item in value):
        return False
    try:
        x0, y0, x1, y1 = [int(item) for item in value]
    except (TypeError, ValueError):
        return False
    return x0 < x1 and y0 < y1


def _box_in_layout_bounds(value: list[int]) -> bool:
    x0, y0, x1, y1 = [int(item) for item in value]
    return (
        0 <= x0 <= 1000
        and 0 <= x1 <= 1000
        and 0 <= y0 <= 1000
        and 0 <= y1 <= 1000
    )


def _line_category_heading(line: dict[str, Any]) -> str | None:
    if any(word.get("labels") for word in line.get("words", [])):
        return None
    tokens = [
        str(word.get("text") or "").strip().upper().strip(":")
        for word in line.get("words", [])
    ]
    tokens = [token for token in tokens if token]
    if not tokens or len(tokens) > 3:
        return None
    text = " ".join(tokens)
    reject = {
        "AGAIN",
        "BALANCE",
        "BALANCE DUE",
        "CHANGE",
        "COME",
        "COME AGAIN",
        "CREDIT",
        "DEBIT",
        "ITEMS SOLD",
        "INSTANT SAVINGS",
        "PLEASE",
        "PLEASE COME",
        "PLEASE COME AGAIN",
        "POLICY INFORMATION",
        "RESP",
        "RESP APPROVED",
        "SELF CHECKOUT",
        "SUBTOTAL",
        "TAX",
        "THANK",
        "TOTAL",
        "TOTAL TAX",
        "VERIFIED BY PIN",
    }
    if text in reject:
        return None
    noisy_terms = {
        "APPROVED",
        "AUTH",
        "PIN",
        "POLICY",
        "RESP",
        "RETURN",
        "SOLD",
        "SWIPED",
        "THANK",
    }
    if set(tokens) & noisy_terms:
        return None
    if any(len(token) < 3 for token in tokens):
        return None
    if not all(token.replace("&", "").isalpha() for token in tokens):
        return None
    return text if text in GENERIC_CATEGORY_HEADINGS else None


def _base_overlap_count(receipt: dict[str, Any]) -> int:
    """Pre-existing overlapping word-box pairs in a receipt's own OCR geometry."""
    return _safe_int(
        build_layout_integrity_evidence(receipt).get("overlap_pair_count")
    ) or 0


def _base_layout_counts(receipt: dict[str, Any]) -> tuple[int, int]:
    """Pre-edit (overlap_pair_count, line_inversion_count) from one evidence
    build, for edits that shift real rows and must be scored relative to base."""
    evidence = build_layout_integrity_evidence(receipt)
    return (
        _safe_int(evidence.get("overlap_pair_count")) or 0,
        _safe_int(evidence.get("line_inversion_count")) or 0,
    )


def _choose_base_receipt(
    receipts: list[dict[str, Any]],
    *,
    used: int,
) -> dict[str, Any]:
    preferred = [
        receipt for receipt in receipts if len(receipt.get("words", [])) <= 190
    ]
    # Prefer a geometrically clean base. A receipt whose own OCR carries
    # overlapping or duplicate word boxes (e.g. "COSTCO" + a stray "CO"
    # fragment) cannot yield a high-fidelity synthetic candidate regardless of
    # the edit applied, so rank those last while keeping a deterministic
    # receipt-key tiebreak.
    ranked = sorted(
        ((_base_overlap_count(row), _receipt_key(row), row) for row in (preferred or receipts)),
        key=lambda item: (item[0], item[1]),
    )
    # Rotate only among the cleanest bases (those tied at the minimum overlap).
    # Operations generated late (e.g. hard negatives after several add-items)
    # call with ``used`` past the receipt count; clamping to the last element
    # there would hand them the NOISIEST receipt, so cycle back to a clean base
    # instead — a noisy base can never produce a high-fidelity candidate.
    best_overlap = ranked[0][0]
    cleanest = [row for overlap, _, row in ranked if overlap <= best_overlap]
    return cleanest[used % len(cleanest)]


def _is_catalog_item(item: MerchantLineItem) -> bool:
    if item.amount <= Decimal("0.00"):
        return False
    text = _normalize_product_text(item.product_text)
    if not text or text.replace(".", "").isdigit():
        return False
    return len(text.split()) <= 7


def _has_similar_product(
    product_text: str,
    existing_product_texts: list[str],
) -> bool:
    target = _product_identity_tokens(product_text)
    normalized = _normalize_product_text(product_text)
    for existing in existing_product_texts:
        if _normalize_product_text(existing) == normalized:
            return True
        existing_tokens = _product_identity_tokens(existing)
        if target and existing_tokens and target & existing_tokens:
            return True
    return False


def _product_identity_tokens(product_text: str) -> set[str]:
    stop_words = {"FRESH", "ORG", "ORGANIC", "THE", "GREEN", "YELLOW", "RED"}
    tokens = set()
    for token in _normalize_product_text(product_text).split():
        token = token.strip("-_/")
        if not token or token in stop_words or len(token) <= 2:
            continue
        if token.endswith("S") and len(token) > 4:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _span_lines(
    receipt: dict[str, Any], *indices: int
) -> list[dict[str, Any]]:
    """Contiguous receipt lines spanning min(indices)..max(indices) inclusive."""
    lines = receipt.get("lines") or []
    if not indices:
        return []
    lo, hi = min(indices), max(indices)
    return [lines[i] for i in range(lo, hi + 1) if 0 <= i < len(lines)]


# Receipt tax-class flags. They print as a SHORT standalone token after the
# price (e.g. "MILK 3.99 F" = non-taxable food, "SOAP 5.99 T" = taxable), or
# suffixed to the price ("5.99T"). Matched as whole tokens so a product word
# that merely ends in 'T' (YOGURT, MINT, OAT) is NOT mistaken for a taxable flag.
# Only the unambiguous taxable markers seen in real receipts. 'X'/'TX' are
# deliberately excluded: they double as size markers / a state abbreviation, and
# a FALSE taxable corrupts recomputed tax, so anything ambiguous stays
# non-taxable (the safe error direction for the tax model).
_TAXABLE_FLAGS = frozenset({"T", "TT"})
_NONTAXABLE_FLAGS = frozenset({"F", "FF", "FT", "FS", "N", "NT"})


def _word_taxable_signal(text: Any) -> bool | None:
    """True/False if a word is a taxable/non-taxable flag, else None."""
    token = str(text or "").strip().upper().rstrip(".,")
    if token in _TAXABLE_FLAGS:
        return True
    if token in _NONTAXABLE_FLAGS:
        return False
    # Flag fused to the price column, e.g. "3.99T" / "12.50F".
    match = re.fullmatch(r"\$?\d+\.\d{2}([A-Z]{1,2})", token)
    if match:
        suffix = match.group(1)
        if suffix in _TAXABLE_FLAGS:
            return True
        if suffix in _NONTAXABLE_FLAGS:
            return False
    return None


def _line_is_taxable(*lines: dict[str, Any]) -> bool:
    """An item is taxable only when it carries an EXPLICIT taxable flag.

    A non-taxable (food) flag or no flag at all reads as non-taxable, so the tax
    model only ever recomputes tax for items the receipt clearly marks taxable —
    avoiding the old ``endswith("T")`` heuristic that flagged YOGURT/MINT/etc.
    """
    return any(
        _word_taxable_signal(word.get("text")) is True
        for line in lines
        for word in line.get("words", [])
    )


# Phrases that denote an item-COUNT summary, matched against the line text with
# underscores normalized to spaces ("ITEMS_SOLD" -> "items sold"). Phrase-based
# (not a bare "number"/"count" keyword) so an item/transaction IDENTIFIER line
# such as "ITEM NUMBER 12345" is NOT mistaken for a counter and rewritten.
_ITEM_COUNT_PHRASES = (
    "items sold",
    "item sold",
    "number of items",
    "number of item",
    "total items",
    "total item",
    "item count",
    "count of items",
    "items purchased",
    "no. of items",
    "# of items",
    "qty sold",
)


def _reconcile_item_count(
    receipt: dict[str, Any], *, delta_count: int
) -> int:
    """Adjust an item-count summary field ("ITEMS SOLD 4") by ``delta_count``.

    The arithmetic gate reconciles currency totals only, leaving item counters
    stale after an add/remove. Match a line whose text denotes an item COUNT
    (not an item/transaction number) and bump its integer (on the same line, or
    the immediately following line if the label and number are split). Returns
    the number of fields updated.
    """
    if not delta_count:
        return 0
    lines = receipt.get("lines") or []
    updated = 0
    for index, line in enumerate(lines):
        words = line.get("words") or []
        joined = " ".join(str(word.get("text") or "") for word in words)
        low = joined.lower().replace("_", " ")
        if not any(phrase in low for phrase in _ITEM_COUNT_PHRASES):
            continue
        target_words = list(words)
        if not any(
            re.fullmatch(r"\d+", str(word.get("text") or "").strip())
            for word in target_words
        ) and index + 1 < len(lines):
            target_words = lines[index + 1].get("words") or []
        for word in reversed(target_words):
            text = str(word.get("text") or "").strip()
            if re.fullmatch(r"\d+", text):
                word["text"] = str(max(0, int(text) + delta_count))
                updated += 1
                break
    return updated


# Default row pitch used only when no real row geometry can be measured at all
# (no matched items AND no labeled item-region rows). Kept as the historical
# constant so well-formed receipts are unaffected.
_DEFAULT_LINE_STEP = 26


def _row_pitch(centers: list[float]) -> int | None:
    """Median vertical gap between consecutive row centers, clamped to the
    realistic single-row pitch range. ``None`` when fewer than two distinct rows
    exist (no measurable pitch)."""
    ordered = sorted({round(value, 1) for value in centers}, reverse=True)
    gaps = [
        abs(ordered[idx] - ordered[idx + 1])
        for idx in range(len(ordered) - 1)
        if abs(ordered[idx] - ordered[idx + 1]) >= 8
    ]
    if not gaps:
        return None
    return max(18, min(44, int(round(statistics.median(gaps)))))


def _label_row_centers(receipt: dict[str, Any], label: str) -> list[float]:
    """Vertical centers (in the 0-1000 ``center_y`` frame used by line items) of
    every receipt line carrying ``label`` — the labeled item-region row rhythm,
    independent of whether each row also matched a paired line item."""
    centers: list[float] = []
    for line in receipt.get("lines", []) or []:
        ys = [
            _cy(word["bbox"])
            for word in line.get("words", []) or []
            if label in (word.get("labels") or [])
        ]
        if ys:
            centers.append(statistics.median(ys))
    return centers


def _line_step(
    items: list[MerchantLineItem],
    receipt: dict[str, Any] | None = None,
    *,
    allow_font_geometry_fallback: bool = False,
) -> int:
    """Estimate the merchant's single item-row pitch.

    Matched line items are the most precise signal, so they are used first.
    When labeling is sparse — common for thin merchants where PRODUCT_NAME /
    LINE_TOTAL pairing yields fewer than two matched items — the row rhythm is
    instead measured from the receipt's labeled item-region rows (LINE_TOTAL
    first, then PRODUCT_NAME). This keeps the geometry comparison anchored to the
    merchant's real row spacing instead of collapsing to a flat constant that no
    real or synthetic receipt actually matches. The constant fallback is reached
    only when no row geometry exists at all.

    ``allow_font_geometry_fallback`` is opt-in and set ONLY by row-GENERATION
    /layout call sites. When a receipt has no measurable row geometry, those
    sites prefer PR #994's merchant row pitch (``font_geometry.line_step_px``)
    over the flat constant. Structure SCORING / signature call sites leave it
    off so the profile can never influence the structure-similarity gate or the
    emitted structure evidence — they stay on measured geometry plus the
    constant.
    """
    pitch = _row_pitch([item.center_y for item in items])
    if pitch is not None:
        return pitch
    if receipt is not None:
        for label in ("LINE_TOTAL", "PRODUCT_NAME"):
            pitch = _row_pitch(_label_row_centers(receipt, label))
            if pitch is not None:
                return pitch
        if allow_font_geometry_fallback:
            profile_step = _font_geometry_px(
                receipt.get("font_geometry") or {}, "line_step_px", default=0
            )
            if profile_step:
                return profile_step
    return _DEFAULT_LINE_STEP


def _label_x_p50(receipt: dict[str, Any], label: str) -> float | None:
    xs = [
        _cx(word["bbox"])
        for line in receipt.get("lines", [])
        for word in line.get("words", [])
        if label in word.get("labels", [])
    ]
    summary = _summary(xs)
    return summary.p50 if summary else None


def _label_right_x_p50(receipt: dict[str, Any], label: str) -> float | None:
    """Median of the RIGHT edges (``bbox[2]``) of tokens carrying ``label`` — the
    real right-edge column for that label (e.g. the price column a receipt's
    LINE_TOTAL amounts hard-align to), as opposed to the center-based
    ``_label_x_p50``. Non-positive / missing right edges are treated as
    unmeasured so a sparse scaffold falls back to its center column instead of
    snapping prices to x=0."""
    xs = [
        float(bbox[2])
        for line in receipt.get("lines", [])
        for word in line.get("words", [])
        if label in word.get("labels", [])
        for bbox in (word.get("bbox"),)
        if isinstance(bbox, list) and len(bbox) == 4 and bbox[2] > 0
    ]
    summary = _summary(xs)
    return summary.p50 if summary else None


def _summary(values: list[float | int]) -> GeometrySummary:
    numeric = sorted(float(value) for value in values)
    if not numeric:
        return GeometrySummary(n=0, p10=0.0, p50=0.0, p90=0.0)
    return GeometrySummary(
        n=len(numeric),
        p10=round(_percentile(numeric, 0.10), 1),
        p50=round(statistics.median(numeric), 1),
        p90=round(_percentile(numeric, 0.90), 1),
    )


def _percentile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    idx = round(q * (len(values) - 1))
    return values[max(0, min(len(values) - 1, idx))]


def _parse_money(value: Any) -> Decimal | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    text = text.replace("USD$", "").replace("$", "").replace(",", "")
    text = text.rstrip("T")
    try:
        return _money(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _format_money(value: Decimal) -> str:
    return f"{_money(value):.2f}"


def _format_money_like(original: Any, value: Decimal) -> str:
    text = str(original or "")
    formatted = _format_money(value)
    if text.strip().upper().startswith("USD$"):
        return f"USD${formatted}"
    if text.strip().startswith("$"):
        return f"${formatted}"
    return formatted


def _right_align_money_box(word: dict[str, Any]) -> None:
    width = _token_width(str(word.get("text") or ""))
    bbox = word.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        bbox[0] = max(0, bbox[2] - width)


def _median_money(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    if not ordered:
        return Decimal("0.00")
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return _money(ordered[mid])
    return _money((ordered[mid - 1] + ordered[mid]) / Decimal("2"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_sum(values) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        total += value
    return _money(total)


def _bio_tags(labels: list[str]) -> list[str]:
    tags: list[str] = []
    previous = "O"
    for label in labels:
        if label == "O":
            tags.append("O")
            previous = "O"
        elif label == previous:
            tags.append(f"I-{label}")
        else:
            tags.append(f"B-{label}")
            previous = label
    return tags


def _first_label(labels: list[str]) -> str:
    for label in labels:
        normalized = _normalize_label(label)
        if normalized != "O":
            return normalized
    return "O"


def _normalize_label(label: Any) -> str:
    value = str(label or "").strip().upper().replace(" ", "_")
    if value.startswith("B-") or value.startswith("I-"):
        value = value[2:]
    if value == "PHONE":
        value = "PHONE_NUMBER"
    return value if value in CORE_LABEL_SET else "O"


def _coerce_bbox(value: Any, x_value: Any, y_value: Any) -> list[int]:
    if (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(coord, int | float) for coord in value)
    ):
        x0, y0, x1, y1 = [int(round(coord)) for coord in value]
        x0, x1 = sorted((max(0, x0), min(1000, x1)))
        y0, y1 = sorted((max(0, y0), min(1000, y1)))
        return [x0, y0, x1, y1]

    x = int(round(_safe_float(x_value, 0.5) * 1000))
    y = int(round(_safe_float(y_value, 0.5) * 1000))
    return [
        max(0, x - 30),
        max(0, y - 12),
        min(1000, x + 50),
        min(1000, y + 12),
    ]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _maybe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_y(line: dict[str, Any]) -> float:
    words = line.get("words") or []
    if words:
        return statistics.median(_cy(word["bbox"]) / 1000 for word in words)
    return _safe_float(line.get("y"), 0.5)


def _cx(bbox: list[int]) -> float:
    return (bbox[0] + bbox[2]) / 2


def _cy(bbox: list[int]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _token_width(token: str) -> int:
    return max(34, min(140, 10 + len(str(token)) * 9))


def _normalize_product_text(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


def _sequence_similarity(left: list[str], right: list[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return float(SequenceMatcher(None, left, right).ratio())


def _set_similarity(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _ratio_close(left: int, right: int) -> float:
    denominator = max(left, right, 1)
    return max(0.0, 1.0 - abs(left - right) / denominator)


def _distance_score(distance: float, *, scale: float) -> float:
    return max(0.0, 1.0 - min(distance / scale, 1.0))


def _refresh_words(receipt: dict[str, Any]) -> None:
    receipt["words"] = [
        word
        for line in receipt.get("lines", [])
        for word in line.get("words", [])
    ]


def _receipt_key(receipt: dict[str, Any]) -> str:
    image_id = str(receipt.get("image_id") or "unknown")
    receipt_num = receipt.get("receipt_num")
    if receipt_num is None:
        raw_receipt_id = str(receipt.get("receipt_id") or "00001")
        receipt_num = raw_receipt_id.rsplit("_", maxsplit=1)[-1]
    try:
        suffix = f"{int(receipt_num):05d}"
    except (TypeError, ValueError):
        suffix = str(receipt_num)
    return f"{image_id}#{suffix}"


def _dedupe(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _slug(value: str) -> str:
    slug = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            slug.append(char)
            previous_dash = False
        elif not previous_dash:
            slug.append("-")
            previous_dash = True
    return "".join(slug).strip("-")[:120] or "merchant-synthetic"
