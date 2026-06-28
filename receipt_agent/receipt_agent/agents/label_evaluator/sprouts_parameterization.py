"""Sprouts-specific receipt parameterization and high-fidelity synthesis.

This module is intentionally narrow. Sprouts has enough receipts to learn a
merchant-local template, so we can synthesize fewer examples by cloning real
receipt geometry and adding one targeted hard-negative line instead of using a
generic placeholder receipt.
"""

from __future__ import annotations

import copy
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
import statistics
from typing import Any

from receipt_dynamo.constants import CORE_LABELS

from receipt_agent.agents.label_evaluator.merchant_synthesis import (
    build_layout_integrity_evidence,
    build_nearest_real_structure_evidence,
    build_synthesis_candidate_quality,
    build_synthesis_accuracy_evidence,
    build_synthetic_receipt_preview,
    compare_structure_to_real_baseline,
)


CORE_LABEL_SET = set(CORE_LABELS.keys())
SPROUTS_MERCHANT = "Sprouts Farmers Market"
UNKNOWN_CATEGORY = "UNCATEGORIZED"
SPROUTS_CATEGORY_HEADINGS = {
    "BAKERY",
    "BEER",
    "BULK",
    "CRV",
    "DAIRY",
    "DELI",
    "FLORAL",
    "FROZEN",
    "GROCERY",
    "MEAT",
    "PRODUCE",
    "SEAFOOD",
    "VITAMINS",
    "WINE",
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
class SproutsSlot:
    """A stable region in the Sprouts receipt template."""

    name: str
    labels: list[str]
    x: GeometrySummary | None
    y: GeometrySummary | None
    examples: list[str]
    synthesis_role: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "labels": self.labels,
            "x": self.x.to_dict() if self.x else None,
            "y": self.y.to_dict() if self.y else None,
            "examples": self.examples,
            "synthesis_role": self.synthesis_role,
            "required": self.required,
        }


@dataclass
class SproutsReceiptParameters:
    """Parameterized structure learned from Sprouts receipts."""

    merchant_name: str
    receipt_count: int
    source_receipt_keys: list[str]
    word_count: GeometrySummary | None
    slots: dict[str, SproutsSlot]
    product_name_patterns: list[str]
    mutation_slots: list[dict[str, Any]]
    generation_limits: dict[str, Any]
    category_patterns: dict[str, Any] = field(default_factory=dict)
    real_structure_baseline: dict[str, Any] = field(default_factory=dict)
    observed_item_catalog: list[dict[str, Any]] = field(default_factory=list)
    confidence_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant_name": self.merchant_name,
            "receipt_count": self.receipt_count,
            "source_receipt_keys": self.source_receipt_keys,
            "word_count": (
                self.word_count.to_dict() if self.word_count else None
            ),
            "slots": {
                key: value.to_dict() for key, value in self.slots.items()
            },
            "product_name_patterns": self.product_name_patterns,
            "mutation_slots": self.mutation_slots,
            "generation_limits": self.generation_limits,
            "category_patterns": self.category_patterns,
            "real_structure_baseline": self.real_structure_baseline,
            "observed_item_catalog": self.observed_item_catalog,
            "confidence_notes": self.confidence_notes,
        }


@dataclass
class SproutsArithmeticCandidate:
    """One arithmetic-consistent Sprouts receipt mutation."""

    candidate: dict[str, Any]
    operation: str
    old_grand_total: Decimal
    new_grand_total: Decimal


@dataclass
class SproutsLineItem:
    """A parsed Sprouts line item row."""

    line_index: int
    line_indices: list[int]
    amount: Decimal
    product_text: str
    center_y: float
    taxable: bool
    category: str | None = None


@dataclass
class SproutsArithmeticAnalysis:
    """Parsed arithmetic anchors from one Sprouts receipt."""

    receipt: dict[str, Any]
    line_items: list[SproutsLineItem]
    grand_total: Decimal
    grand_total_line_indices: list[int]
    section_headings: list["SproutsSectionHeading"] = field(
        default_factory=list
    )

    @property
    def subtotal(self) -> Decimal:
        return _money_sum(item.amount for item in self.line_items)

    @property
    def category_sequence(self) -> list[str]:
        return [heading.category for heading in self.section_headings]


@dataclass
class SproutsSectionHeading:
    """A category heading such as PRODUCE or DAIRY."""

    category: str
    line_index: int
    center_y: float


@dataclass
class SproutsItemCatalogEntry:
    """Observed Sprouts item evidence grouped by text and category."""

    product_text: str
    amount: Decimal
    category: str
    taxable: bool
    count: int
    source_receipt_keys: list[str]

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
class SproutsAddItemPlan:
    """A category-aware add-item mutation plan."""

    analysis: SproutsArithmeticAnalysis
    item: SproutsItemCatalogEntry
    y_center: float
    shift_anchor_y: float | None
    shift_delta: int
    seen_in_other_receipt: bool
    selection_reason: str
    # "down" pushes the lines below the insertion point toward the receipt
    # bottom; "up" pushes the lines above it toward the top. Whichever side has
    # whitespace is used so a densely packed receipt can still host one row.
    shift_direction: str = "down"


def is_sprouts_merchant(merchant_name: str | None) -> bool:
    """Return True when the merchant is the Sprouts chain."""
    normalized = (merchant_name or "").strip().lower()
    return normalized in {
        "sprouts farmers market",
        "sprouts farmer's market",
        "sprouts",
    }


def build_sprouts_receipt_parameters(
    receipts_data: list[dict[str, Any]],
) -> SproutsReceiptParameters | None:
    """Learn a compact Sprouts template from structured receipt data."""
    normalized_receipts = [
        receipt
        for receipt in (
            _normalize_receipt(receipt) for receipt in receipts_data
        )
        if receipt["words"]
    ]
    if not normalized_receipts:
        return None

    slots = {
        "merchant_header": _slot(
            "merchant_header",
            ["MERCHANT_NAME"],
            normalized_receipts,
            "Preserve the centered SPROUTS / FARMERS MARKET header.",
        ),
        "address_block": _slot(
            "address_block",
            ["ADDRESS_LINE"],
            normalized_receipts,
            "Preserve the real store address; add only O hard negatives nearby.",
        ),
        "contact_block": _slot(
            "contact_block",
            ["PHONE_NUMBER", "STORE_HOURS"],
            normalized_receipts,
            "Keep phone and store-hours lines as header anchors.",
        ),
        "product_column": _slot(
            "product_column",
            ["PRODUCT_NAME"],
            normalized_receipts,
            "Left-column grocery product descriptions.",
        ),
        "line_total_column": _slot(
            "line_total_column",
            ["LINE_TOTAL"],
            normalized_receipts,
            "Right-column item prices aligned with product rows.",
        ),
        "grand_total_column": _slot(
            "grand_total_column",
            ["GRAND_TOTAL"],
            normalized_receipts,
            "Right-column final amount; add nearby O amounts sparingly.",
        ),
        "discount_like_zone": _slot(
            "discount_like_zone",
            ["DISCOUNT"],
            normalized_receipts,
            "Sparse discount evidence; synthesize at most one O hard negative.",
            required=False,
        ),
    }

    product_examples = _examples_for_labels(
        normalized_receipts, ["PRODUCT_NAME"]
    )
    product_name_patterns = [
        "Uppercase grocery descriptions in the left column.",
        "Organic prefixes such as ORG or ORGANIC are common.",
        "Percent-bearing product descriptors like 6% FAT are product text.",
        "Slash-bearing product descriptors like A2/A2 occur inside product names.",
    ]
    if not any("%" in example for example in product_examples):
        product_name_patterns = product_name_patterns[:2]

    arithmetic_analyses = [
        analysis
        for analysis in (
            _analyze_arithmetic_receipt(receipt)
            for receipt in normalized_receipts
        )
        if analysis
    ]
    item_catalog = _build_item_catalog(arithmetic_analyses)

    return SproutsReceiptParameters(
        merchant_name=SPROUTS_MERCHANT,
        receipt_count=len(normalized_receipts),
        source_receipt_keys=[
            _receipt_key(receipt) for receipt in normalized_receipts
        ],
        word_count=_summary(
            [len(receipt["words"]) for receipt in normalized_receipts]
        ),
        slots=slots,
        product_name_patterns=product_name_patterns,
        mutation_slots=[
            {
                "target": "O -> ADDRESS_LINE",
                "slot": "address_block",
                "mutation": "Insert one centered O-labeled loyalty/header line near the address block.",
                "max_examples": 1,
            },
            {
                "target": "O -> DISCOUNT",
                "slot": "product_column",
                "mutation": "Insert one O-labeled weekly-special line in the item band without changing product labels.",
                "max_examples": 1,
            },
            {
                "target": "O -> GRAND_TOTAL",
                "slot": "grand_total_column",
                "mutation": "Insert one O-labeled tender/change amount near totals while preserving the real grand total.",
                "max_examples": 1,
            },
            {
                "target": "LINE_ITEM_ARITHMETIC_ADD",
                "slot": "product_column",
                "mutation": "Add one non-taxable grocery item in an open item-band gap and increase matching final/payment totals.",
                "max_examples": 1,
            },
            {
                "target": "LINE_ITEM_ARITHMETIC_REMOVE",
                "slot": "product_column",
                "mutation": "Remove one non-taxable grocery item, close the vertical gap, and decrease matching final/payment totals.",
                "max_examples": 1,
            },
        ],
        generation_limits={
            "max_candidates_per_training_run": 5,
            "max_arithmetic_candidates_per_training_run": 2,
            "max_mutation_lines_per_candidate": 1,
            "validation_split": "real_receipts_only",
            "prefer_base_receipts_under_tokens": 185,
        },
        category_patterns=_summarize_category_patterns(arithmetic_analyses),
        real_structure_baseline=_build_real_structure_baseline(
            arithmetic_analyses
        ),
        observed_item_catalog=[entry.to_dict() for entry in item_catalog[:20]],
        confidence_notes=[
            "Built from VALID word labels only.",
            "Synthetic examples clone real receipt token order and geometry.",
            "Each candidate mutates one confusion target to reduce overtraining risk.",
            "Arithmetic item edits prefer observed Sprouts item text/category/price evidence.",
            "Add-item synthesis requires the item to be observed on a different Sprouts receipt.",
            "Arithmetic candidates currently use non-taxable item edits, so tax deltas remain 0.00.",
        ],
    )


def generate_parameterized_sprouts_candidates(
    plan: dict[str, Any],
    receipts_data: list[dict[str, Any]],
    *,
    max_candidates: int = 3,
) -> list[dict[str, Any]]:
    """Generate train-only LayoutLM candidates from real Sprouts geometry."""
    parameters = build_sprouts_receipt_parameters(receipts_data)
    if parameters is None:
        return []

    base_receipts = [
        receipt
        for receipt in (
            _normalize_receipt(receipt) for receipt in receipts_data
        )
        if receipt["words"]
    ]
    if not base_receipts:
        return []
    base_receipts.sort(
        key=lambda receipt: (len(receipt["words"]), _receipt_key(receipt))
    )
    analyses = [
        analysis
        for analysis in (
            _analyze_arithmetic_receipt(receipt) for receipt in base_receipts
        )
        if analysis
    ]

    recipes = [
        recipe
        for recipe in plan.get("recipes", [])
        if recipe.get("error_kind") == "false_positive"
        and recipe.get("actual_label") == "O"
    ]
    recipes.sort(
        key=lambda recipe: {
            "ADDRESS_LINE": 0,
            "DISCOUNT": 1,
            "GRAND_TOTAL": 2,
        }.get(str(recipe.get("predicted_label")), 99)
    )

    # Each recipe targets one model confusion (e.g. ADDRESS_LINE precision).
    # The original loop emitted at most one hard negative per label, which
    # capped this path at the number of distinct recipes (~3). The same
    # mutation is equally valid on every real base layout, so emit one
    # candidate per (label, base receipt) — distinct real geometry, distinct
    # candidate id — and let the loader's structure gate accept the strong
    # ones. Round-robin across labels so no single confusion dominates.
    candidates: list[dict[str, Any]] = []
    used_pairs: set[tuple[str, str]] = set()
    for base_offset in range(len(base_receipts)):
        if len(candidates) >= max_candidates:
            break
        base = _choose_base_receipt(base_receipts, used=base_offset)
        base_key = _receipt_key(base)
        for recipe in recipes:
            if len(candidates) >= max_candidates:
                break
            predicted_label = str(recipe.get("predicted_label") or "")
            pair = (predicted_label, base_key)
            if pair in used_pairs:
                continue
            mutation = _mutation_for_label(predicted_label, parameters)
            if mutation is None:
                continue
            candidate = _candidate_from_base(
                base,
                parameters,
                recipe,
                mutation,
                analyses,
                index=len(candidates) + 1,
            )
            if candidate:
                candidates.append(candidate)
                used_pairs.add(pair)

    return candidates


def generate_arithmetic_sprouts_candidates(
    receipts_data: list[dict[str, Any]],
    *,
    max_candidates: int = 2,
) -> list[dict[str, Any]]:
    """Generate Sprouts variants with line-item and total arithmetic."""
    parameters = build_sprouts_receipt_parameters(receipts_data)
    if parameters is None:
        return []

    analyses = [
        analysis
        for analysis in (
            _analyze_arithmetic_receipt(_normalize_receipt(receipt))
            for receipt in receipts_data
        )
        if analysis
    ]
    if not analyses:
        return []

    catalog = _build_item_catalog(analyses)
    add_plans = _rank_add_item_plans(analyses, catalog)
    remove_analyses = _rank_remove_item_analyses(analyses)

    add_candidates: list[dict[str, Any]] = []
    for plan in add_plans:
        if len(add_candidates) >= max_candidates:
            break
        built = _generate_add_item_candidate(
            parameters, analyses, plan=plan
        )
        if built:
            add_candidates.append(built.candidate)

    remove_candidates: list[dict[str, Any]] = []
    for analysis in remove_analyses:
        if len(remove_candidates) >= max_candidates:
            break
        built = _generate_remove_item_candidate(
            parameters, analyses, analysis=analysis
        )
        if built:
            remove_candidates.append(built.candidate)

    # Interleave add/remove so the accepted mix stays balanced rather than
    # saturating one operation, mirroring the concentration guard the loader
    # and audit apply downstream.
    candidates: list[dict[str, Any]] = []
    add_iter = iter(add_candidates)
    remove_iter = iter(remove_candidates)
    while len(candidates) < max_candidates:
        added = next(add_iter, None)
        if added is not None:
            candidates.append(added)
            if len(candidates) >= max_candidates:
                break
        removed = next(remove_iter, None)
        if removed is not None:
            candidates.append(removed)
        if added is None and removed is None:
            break

    return candidates[:max_candidates]


def _normalize_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    lines = []
    words = []
    source_lines = receipt.get("all_lines") or receipt.get("lines", []) or []
    for line_index, line in enumerate(source_lines):
        line_words = []
        line_id = int(line.get("line_id") or line_index + 1)
        line_y = _safe_float(line.get("y"), 0.5)
        for word_index, word in enumerate(line.get("words", []) or []):
            text = str(word.get("text") or "").strip()
            if not text:
                continue
            bbox = _coerce_bbox(word.get("bbox"), word.get("x"), line_y)
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
                "bbox": bbox,
                "labels": labels,
                "line_id": line_id,
                "word_id": int(word.get("word_id") or word_index + 1),
            }
            line_words.append(row)
            words.append(row)
        if line_words:
            line_words.sort(key=lambda row: (row["bbox"][0], row["word_id"]))
            lines.append(
                {
                    "line_id": line_id,
                    "y": line_y,
                    "words": line_words,
                }
            )

    lines.sort(key=lambda line: -_line_y(line))
    return {
        "receipt_id": receipt.get("receipt_id"),
        "image_id": receipt.get("image_id"),
        "receipt_num": receipt.get("receipt_num"),
        "lines": lines,
        "words": words,
    }


def _slot(
    name: str,
    labels: list[str],
    receipts: list[dict[str, Any]],
    synthesis_role: str,
    *,
    required: bool = True,
) -> SproutsSlot:
    matching = [
        word
        for receipt in receipts
        for word in receipt["words"]
        if set(word["labels"]) & set(labels)
    ]
    return SproutsSlot(
        name=name,
        labels=labels,
        x=_summary([_cx(word["bbox"]) for word in matching]),
        y=_summary([_cy(word["bbox"]) for word in matching]),
        examples=_dedupe([word["text"] for word in matching])[:12],
        synthesis_role=synthesis_role,
        required=required,
    )


def _candidate_from_base(
    receipt: dict[str, Any],
    parameters: SproutsReceiptParameters,
    recipe: dict[str, Any],
    mutation: dict[str, Any],
    analyses: list[SproutsArithmeticAnalysis],
    *,
    index: int,
) -> dict[str, Any] | None:
    mutated_receipt = copy.deepcopy(receipt)
    mutated_lines = mutated_receipt["lines"]
    mutation = dict(mutation)
    mutation["y"] = _nearest_open_y(receipt, mutation)
    inserted = _build_mutation_line(mutation)
    if inserted is None:
        return None

    insert_at = 0
    inserted_y = _line_y(inserted)
    for idx, line in enumerate(mutated_lines):
        if _line_y(line) < inserted_y:
            insert_at = idx
            break
    else:
        insert_at = len(mutated_lines)
    mutated_lines.insert(insert_at, inserted)
    _refresh_receipt_words(mutated_receipt)

    tokens, bboxes, ner_tags = _flatten_receipt_lines(mutated_lines)

    if not tokens or len(tokens) > 220:
        return None

    target = str(recipe.get("predicted_label") or mutation["predicted_label"])
    base_key = _receipt_key(receipt)
    slug = _slug(f"sprouts-{index}-{target}-{base_key}")
    candidate_metadata = {
        "source": "sprouts_parameterized_geometry",
        "parameterization_version": "sprouts-v1",
        "base_receipt_key": base_key,
        "operation": "hard_negative",
        "actual_label": "O",
        "predicted_label": target,
        "error_kind": "false_positive",
        "mutation": mutation["name"],
        "target_zone": mutation["target_zone"],
        "structure_similarity": _score_structure_similarity(
            mutated_receipt,
            analyses,
        ),
        "expected_label_effect": (
            f"Improve {target} precision with one high-fidelity O hard negative."
        ),
        "train_only_reason": (
            "Synthetic examples target known model confusions and must not enter validation."
        ),
    }
    candidate_metadata["layout_integrity"] = build_layout_integrity_evidence(
        mutated_receipt
    )
    candidate_metadata["candidate_quality"] = (
        build_synthesis_candidate_quality(
            "hard_negative",
            candidate_metadata,
            token_count=len(tokens),
        )
    )
    candidate_metadata["synthetic_receipt_preview"] = (
        build_synthetic_receipt_preview(mutated_receipt, candidate_metadata)
    )
    candidate_metadata["synthesis_accuracy_evidence"] = (
        build_synthesis_accuracy_evidence("hard_negative", candidate_metadata)
    )
    return {
        "candidate_id": slug,
        "recipe_id": str(recipe.get("recipe_id") or f"sprouts-{target}"),
        "merchant_name": parameters.merchant_name,
        "tokens": tokens,
        "bboxes": bboxes,
        "ner_tags": ner_tags,
        "receipt_key": f"synthetic-{slug}#00001",
        "image_id": f"synthetic-{slug}",
        "train_only": True,
        "metadata": candidate_metadata,
    }


def _generate_add_item_candidate(
    parameters: SproutsReceiptParameters,
    analyses: list[SproutsArithmeticAnalysis],
    *,
    plan: SproutsAddItemPlan | None = None,
) -> SproutsArithmeticCandidate | None:
    if plan is None:
        catalog = _build_item_catalog(analyses)
        plan = _choose_add_item_plan(analyses, catalog)
    if plan is None:
        return None

    analysis = plan.analysis
    receipt = copy.deepcopy(analysis.receipt)
    refreshed = _analyze_arithmetic_receipt(receipt)
    if refreshed is None:
        return None
    base_layout_counts = _base_layout_counts(refreshed.receipt)

    added_amount = plan.item.amount
    old_total = refreshed.grand_total
    new_total = _money(old_total + added_amount)
    if plan.shift_anchor_y is not None and plan.shift_delta > 0:
        if plan.shift_direction == "up":
            _shift_lines_above_up(
                receipt, plan.shift_anchor_y, plan.shift_delta
            )
        else:
            _shift_lines_below_down(
                receipt, plan.shift_anchor_y, plan.shift_delta
            )
    added_line = _build_line_item_line(
        parameters,
        plan.item.product_tokens,
        added_amount,
        y_center=plan.y_center,
    )
    if added_line is None:
        return None

    _insert_line_sorted(receipt, added_line)
    arithmetic = _apply_non_taxable_delta(
        receipt,
        refreshed,
        delta=added_amount,
    )
    candidate = _candidate_from_receipt(
        receipt,
        parameters,
        operation="add_line_item",
        index=1,
        metadata={
            "added_item": {
                "product_text": plan.item.product_text,
                "product_tokens": plan.item.product_tokens,
                "line_total": _format_money(added_amount),
                "category": plan.item.category,
                "taxable": plan.item.taxable,
                "observed_count": plan.item.count,
                "source_receipt_keys": plan.item.source_receipt_keys[:5],
                "seen_in_other_receipt": plan.seen_in_other_receipt,
            },
            "old_grand_total": _format_money(old_total),
            "new_grand_total": _format_money(new_total),
            "old_subtotal": _format_money(refreshed.subtotal),
            "new_subtotal": arithmetic["new_subtotal"],
            "tax_delta": "0.00",
            "arithmetic_reconciliation": arithmetic,
            "category_insertion": {
                "category": plan.item.category,
                "y_center": round(plan.y_center, 1),
                "shifted_lower_lines_by": plan.shift_delta,
                "selection_reason": plan.selection_reason,
            },
            "observed_item_evidence": _observed_item_evidence(
                plan,
                analyses,
            ),
            "structure_similarity": _score_structure_similarity(
                receipt,
                analyses,
            ),
            "balancing_strategy": "add observed non-taxable item, update subtotal/final/payment amounts, and leave tax unchanged",
        },
        base_layout_counts=base_layout_counts,
    )
    return SproutsArithmeticCandidate(
        candidate=candidate,
        operation="add_line_item",
        old_grand_total=old_total,
        new_grand_total=new_total,
    )


def _generate_remove_item_candidate(
    parameters: SproutsReceiptParameters,
    analyses: list[SproutsArithmeticAnalysis],
    *,
    analysis: SproutsArithmeticAnalysis | None = None,
) -> SproutsArithmeticCandidate | None:
    if analysis is None:
        removable = [
            candidate
            for candidate in analyses
            if len(candidate.line_items) >= 3
            and candidate.grand_total_line_indices
            and not _analysis_has_remove_base_blocker(candidate)
        ]
        if not removable:
            return None
        analysis = min(removable, key=_remove_analysis_rank_key)
    receipt = copy.deepcopy(analysis.receipt)
    refreshed = _analyze_arithmetic_receipt(receipt)
    if refreshed is None or len(refreshed.line_items) < 3:
        return None
    base_layout_counts = _base_layout_counts(refreshed.receipt)

    removable_items = [
        item for item in refreshed.line_items if not item.taxable
    ]
    if not removable_items:
        return None
    removed = min(removable_items, key=lambda item: item.amount)
    old_total = refreshed.grand_total
    new_total = _money(max(Decimal("0.00"), old_total - removed.amount))
    removed_center = removed.center_y
    line_step = _line_step(refreshed.line_items)
    for line_index in sorted(set(removed.line_indices), reverse=True):
        if line_index < len(receipt["lines"]):
            del receipt["lines"][line_index]
    _shift_lines_below(receipt, removed_center, line_step)
    _refresh_receipt_words(receipt)
    arithmetic = _apply_non_taxable_delta(
        receipt,
        refreshed,
        delta=-removed.amount,
    )
    candidate = _candidate_from_receipt(
        receipt,
        parameters,
        operation="remove_line_item",
        index=2,
        metadata={
            "removed_item": {
                "product_text": removed.product_text,
                "line_total": _format_money(removed.amount),
                "category": removed.category or UNKNOWN_CATEGORY,
                "taxable": removed.taxable,
            },
            "retained_line_item_count": len(refreshed.line_items) - 1,
            "old_grand_total": _format_money(old_total),
            "new_grand_total": _format_money(new_total),
            "old_subtotal": _format_money(refreshed.subtotal),
            "new_subtotal": arithmetic["new_subtotal"],
            "tax_delta": "0.00",
            "arithmetic_reconciliation": arithmetic,
            "structure_similarity": _score_structure_similarity(
                receipt,
                analyses,
            ),
            "balancing_strategy": "remove one non-taxable item, update subtotal/final/payment amounts, and leave tax unchanged",
        },
        base_layout_counts=base_layout_counts,
    )
    return SproutsArithmeticCandidate(
        candidate=candidate,
        operation="remove_line_item",
        old_grand_total=old_total,
        new_grand_total=new_total,
    )


def _rank_remove_item_analyses(
    analyses: list[SproutsArithmeticAnalysis],
) -> list[SproutsArithmeticAnalysis]:
    """Removable receipts (>=3 items, a grand total, a non-taxable item).

    Same gate the single-candidate path used, but returns every qualifying
    base receipt — one removal each — ordered by cohesive product sections
    first, then subtotal, so the visual result stays receipt-like while still
    leaving at least two visible product rows after removal.
    """
    removable = [
        analysis
        for analysis in analyses
        if len(analysis.line_items) >= 3
        and analysis.grand_total_line_indices
        and any(not item.taxable for item in analysis.line_items)
        and not _analysis_has_remove_base_blocker(analysis)
    ]
    removable.sort(key=_remove_analysis_rank_key)
    return removable


def _remove_analysis_rank_key(
    analysis: SproutsArithmeticAnalysis,
) -> tuple[int, Decimal]:
    categories = {
        item.category or UNKNOWN_CATEGORY for item in analysis.line_items
    }
    return (len(categories), analysis.subtotal)


def _analysis_has_remove_base_blocker(
    analysis: SproutsArithmeticAnalysis,
) -> bool:
    """Skip visually confusing receipts as remove-item bases."""
    for line in analysis.receipt.get("lines", []):
        words = line.get("words", []) or []
        compact_line = "".join(
            " ".join(str(word.get("text") or "") for word in words)
            .upper()
            .split()
        )
        if "VOID" in compact_line or "REFUND" in compact_line:
            return True
        for word in words:
            token = str(word.get("text") or "").strip()
            if token.startswith("-") and _parse_money(token[1:]) is not None:
                return True
    return False


def _analyze_arithmetic_receipt(
    receipt: dict[str, Any],
) -> SproutsArithmeticAnalysis | None:
    line_items: list[SproutsLineItem] = []
    grand_total: Decimal | None = None
    grand_total_line_indices: list[int] = []
    line_total_rows: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    section_headings: list[SproutsSectionHeading] = []
    category_by_line_index: dict[int, str] = {}
    current_category: str | None = None

    for line_index, line in enumerate(receipt.get("lines", [])):
        heading = _line_category_heading(line)
        if heading:
            current_category = heading
            section_headings.append(
                SproutsSectionHeading(
                    category=heading,
                    line_index=line_index,
                    center_y=_line_y(line) * 1000,
                )
            )
        category_by_line_index[line_index] = (
            current_category or UNKNOWN_CATEGORY
        )

        product_words = [
            word
            for word in line.get("words", [])
            if "PRODUCT_NAME" in word.get("labels", [])
        ]
        line_total_words = [
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
                    "category": category_by_line_index[line_index],
                }
            )
        if line_total_words:
            amount = _parse_money(line_total_words[-1].get("text"))
            if amount is not None:
                line_total_rows.append(
                    {
                        "line_index": line_index,
                        "line": line,
                        "amount": amount,
                        "center_y": _line_y(line) * 1000,
                        "category": category_by_line_index[line_index],
                    }
                )

        has_grand_total = any(
            "GRAND_TOTAL" in word.get("labels", [])
            for word in line.get("words", [])
        )
        if has_grand_total:
            parsed = [
                _parse_money(word.get("text"))
                for word in line.get("words", [])
                if _parse_money(word.get("text")) is not None
            ]
            if parsed:
                grand_total_line_indices.append(line_index)
                if grand_total is None:
                    grand_total = parsed[-1]

    used_total_lines: set[int] = set()
    for product_row in product_rows:
        total_row = _matching_line_total_row(
            product_row,
            line_total_rows,
            used_total_lines,
        )
        if total_row is None:
            continue
        used_total_lines.add(total_row["line_index"])
        line_indices = sorted(
            {product_row["line_index"], total_row["line_index"]}
        )
        center_y = statistics.median(
            [product_row["center_y"], total_row["center_y"]]
        )
        line_items.append(
            SproutsLineItem(
                line_index=product_row["line_index"],
                line_indices=line_indices,
                amount=total_row["amount"],
                product_text=" ".join(
                    word["text"] for word in product_row["product_words"]
                ),
                center_y=center_y,
                taxable=_line_is_taxable(
                    product_row["line"], total_row["line"]
                ),
                category=_coalesce_category(
                    str(product_row.get("category") or UNKNOWN_CATEGORY),
                    str(total_row.get("category") or UNKNOWN_CATEGORY),
                ),
            )
        )

    if not line_items or grand_total is None:
        return None
    return SproutsArithmeticAnalysis(
        receipt=receipt,
        line_items=line_items,
        grand_total=grand_total,
        grand_total_line_indices=grand_total_line_indices,
        section_headings=section_headings,
    )


def _candidate_from_receipt(
    receipt: dict[str, Any],
    parameters: SproutsReceiptParameters,
    *,
    operation: str,
    index: int,
    metadata: dict[str, Any],
    base_layout_counts: tuple[int, int] | None = None,
) -> dict[str, Any]:
    tokens, bboxes, ner_tags = _flatten_receipt_lines(receipt.get("lines", []))

    slug = _slug(
        f"sprouts-arithmetic-{index}-{operation}-{_receipt_key(receipt)}"
    )
    candidate_metadata = {
        "source": "sprouts_arithmetic_geometry",
        "parameterization_version": "sprouts-v1",
        "base_receipt_key": _receipt_key(receipt),
        "operation": operation,
        "train_only_reason": (
            "Arithmetic synthetic examples must not enter validation."
        ),
        **metadata,
    }
    layout_kwargs: dict[str, Any] = {}
    if base_layout_counts is not None:
        layout_kwargs = {
            "base_overlap_count": base_layout_counts[0],
            "base_line_inversion_count": base_layout_counts[1],
        }
    candidate_metadata["layout_integrity"] = build_layout_integrity_evidence(
        receipt,
        **layout_kwargs,
    )
    candidate_metadata["candidate_quality"] = (
        build_synthesis_candidate_quality(
            operation,
            candidate_metadata,
            token_count=len(tokens),
        )
    )
    candidate_metadata["synthetic_receipt_preview"] = (
        build_synthetic_receipt_preview(receipt, candidate_metadata)
    )
    candidate_metadata["synthesis_accuracy_evidence"] = (
        build_synthesis_accuracy_evidence(operation, candidate_metadata)
    )
    return {
        "candidate_id": slug,
        "recipe_id": f"sprouts-arithmetic-{operation}",
        "merchant_name": parameters.merchant_name,
        "tokens": tokens,
        "bboxes": bboxes,
        "ner_tags": ner_tags,
        "receipt_key": f"synthetic-{slug}#00001",
        "image_id": f"synthetic-{slug}",
        "train_only": True,
        "metadata": candidate_metadata,
    }


def _base_layout_counts(receipt: dict[str, Any]) -> tuple[int, int]:
    evidence = build_layout_integrity_evidence(receipt)
    return (
        int(evidence.get("overlap_pair_count") or 0),
        int(evidence.get("line_inversion_count") or 0),
    )


def _build_line_item_line(
    parameters: SproutsReceiptParameters,
    product_tokens: list[str],
    amount: Decimal,
    *,
    y_center: float,
) -> dict[str, Any] | None:
    product_slot = parameters.slots.get("product_column")
    total_slot = parameters.slots.get("line_total_column")
    if product_slot is None or total_slot is None:
        return None
    product_x = max(
        55, int(round((product_slot.x.p10 if product_slot.x else 90) - 30))
    )
    price_text = _format_money(amount)
    price_width = _token_width(price_text)
    price_center = int(round(total_slot.x.p50 if total_slot.x else 860))
    price_x = max(0, min(1000 - price_width, price_center - price_width // 2))
    y0 = max(0, min(976, int(round(y_center - 12))))

    words: list[dict[str, Any]] = []
    cursor = product_x
    for idx, token in enumerate(product_tokens, start=1):
        width = _token_width(token)
        words.append(
            {
                "text": token,
                "bbox": [cursor, y0, min(1000, cursor + width), y0 + 24],
                "labels": ["PRODUCT_NAME"],
                "line_id": 20_000,
                "word_id": idx,
            }
        )
        cursor = min(price_x - 20, cursor + width + 10)
    words.append(
        {
            "text": price_text,
            "bbox": [price_x, y0, min(1000, price_x + price_width), y0 + 24],
            "labels": ["LINE_TOTAL"],
            "line_id": 20_000,
            "word_id": len(product_tokens) + 1,
        }
    )
    return {"line_id": 20_000, "y": y_center / 1000, "words": words}


def _build_item_catalog(
    analyses: list[SproutsArithmeticAnalysis],
) -> list[SproutsItemCatalogEntry]:
    grouped: dict[tuple[str, str, bool], dict[str, Any]] = {}
    for analysis in analyses:
        receipt_key = _receipt_key(analysis.receipt)
        for item in analysis.line_items:
            if not _is_catalog_item(item):
                continue
            category = item.category or UNKNOWN_CATEGORY
            product_key = _normalize_product_text(item.product_text)
            key = (category, product_key, item.taxable)
            if key not in grouped:
                grouped[key] = {
                    "product_text": item.product_text,
                    "amounts": [],
                    "source_receipt_keys": set(),
                }
            grouped[key]["amounts"].append(item.amount)
            grouped[key]["source_receipt_keys"].add(receipt_key)

    entries = [
        SproutsItemCatalogEntry(
            product_text=str(value["product_text"]),
            amount=_median_money(value["amounts"]),
            category=category,
            taxable=taxable,
            count=len(value["amounts"]),
            source_receipt_keys=sorted(value["source_receipt_keys"]),
        )
        for (category, _product_key, taxable), value in grouped.items()
        if value["amounts"]
    ]
    entries.sort(
        key=lambda entry: (
            entry.category != "PRODUCE",
            "BANANA" not in entry.product_text.upper(),
            -entry.count,
            entry.product_text,
        )
    )
    return entries


def _resolve_shift_direction(
    receipt: dict[str, Any],
    shift_anchor_y: float | None,
    shift_delta: int,
) -> str | None:
    """Pick the shift side that has whitespace, or None if neither does.

    No anchor means no shift is needed (the slot is already empty), so "down"
    is returned as a no-op-compatible default. Otherwise the down side (push
    the block below the insertion point toward the receipt bottom) is preferred
    to preserve historical behaviour, falling back to the up side when the
    bottom is full.
    """
    if shift_anchor_y is None:
        return "down"
    if _can_shift_lines_below_down(receipt, shift_anchor_y, shift_delta):
        return "down"
    if _can_shift_lines_above_up(receipt, shift_anchor_y, shift_delta):
        return "up"
    return None


def _build_add_item_plan(
    analysis: SproutsArithmeticAnalysis,
    entry: SproutsItemCatalogEntry,
    present_products: list[str],
    base_key: str,
) -> tuple[float, SproutsAddItemPlan] | None:
    """Build one grounded, geometry-checked add-item plan, or None.

    Centralizes the per-(receipt, entry) gates so the single-best chooser and
    the per-receipt ranker stay in lock-step: catalog grounding, category
    presence, no duplicate product, a valid insertion anchor, and a shift
    direction with real whitespace.
    """
    if entry.taxable or entry.category == UNKNOWN_CATEGORY:
        return None
    if _has_similar_product(entry.product_text, present_products):
        return None
    anchor = _category_insert_anchor(analysis, entry.category)
    if anchor is None:
        return None
    y_center, shift_anchor_y, shift_delta = anchor
    direction = _resolve_shift_direction(
        analysis.receipt, shift_anchor_y, shift_delta
    )
    if direction is None:
        return None
    seen_in_other_receipt = any(
        source_key != base_key for source_key in entry.source_receipt_keys
    )
    if not seen_in_other_receipt:
        return None
    score = _add_item_plan_score(
        analysis,
        entry,
        seen_in_other_receipt=seen_in_other_receipt,
    )
    return (
        score,
        SproutsAddItemPlan(
            analysis=analysis,
            item=entry,
            y_center=y_center,
            shift_anchor_y=shift_anchor_y,
            shift_delta=shift_delta,
            seen_in_other_receipt=seen_in_other_receipt,
            selection_reason=(
                "observed in another Sprouts receipt with the same category"
            ),
            shift_direction=direction,
        ),
    )


def _choose_add_item_plan(
    analyses: list[SproutsArithmeticAnalysis],
    catalog: list[SproutsItemCatalogEntry],
) -> SproutsAddItemPlan | None:
    plans: list[tuple[float, SproutsAddItemPlan]] = []
    for analysis in analyses:
        if not analysis.section_headings:
            continue
        base_key = _receipt_key(analysis.receipt)
        present_categories = {
            item.category for item in analysis.line_items if item.category
        }
        present_products = [item.product_text for item in analysis.line_items]
        for entry in catalog:
            if entry.category not in present_categories:
                continue
            built = _build_add_item_plan(
                analysis, entry, present_products, base_key
            )
            if built is not None:
                plans.append(built)

    if not plans:
        return None
    plans.sort(key=lambda item: item[0], reverse=True)
    return plans[0][1]


def _rank_add_item_plans(
    analyses: list[SproutsArithmeticAnalysis],
    catalog: list[SproutsItemCatalogEntry],
) -> list[SproutsAddItemPlan]:
    """Best grounded add-item plan per base receipt, ranked high to low.

    ``_choose_add_item_plan`` returns only the single highest-scoring plan,
    which caps the rich Sprouts merchant at one add-item candidate. This keeps
    every per-candidate gate (catalog grounding via ``seen_in_other_receipt``,
    category presence, shiftable geometry) and simply emits one accepted plan
    per distinct base receipt so the generator can scale with the data.
    """
    best_by_receipt: dict[str, tuple[float, SproutsAddItemPlan]] = {}
    for analysis in analyses:
        if not analysis.section_headings:
            continue
        base_key = _receipt_key(analysis.receipt)
        present_categories = {
            item.category for item in analysis.line_items if item.category
        }
        present_products = [item.product_text for item in analysis.line_items]
        for entry in catalog:
            if entry.category not in present_categories:
                continue
            built = _build_add_item_plan(
                analysis, entry, present_products, base_key
            )
            if built is None:
                continue
            score, plan = built
            current = best_by_receipt.get(base_key)
            if current is None or score > current[0]:
                best_by_receipt[base_key] = (score, plan)
    ranked = sorted(
        best_by_receipt.values(), key=lambda item: item[0], reverse=True
    )
    return [plan for _score, plan in ranked]


def _category_insert_anchor(
    analysis: SproutsArithmeticAnalysis,
    category: str,
) -> tuple[float, float | None, int] | None:
    category_items = [
        item for item in analysis.line_items if item.category == category
    ]
    if not category_items:
        headings = [
            heading
            for heading in analysis.section_headings
            if heading.category == category
        ]
        if not headings:
            return None
        line_step = max(26, _line_step(analysis.line_items))
        anchor_y = headings[0].center_y
        return max(24.0, anchor_y - line_step), anchor_y, line_step

    line_step = max(26, _line_step(analysis.line_items))
    lower_item_y = min(item.center_y for item in category_items)
    return max(24.0, lower_item_y - line_step), lower_item_y, line_step


def _add_item_plan_score(
    analysis: SproutsArithmeticAnalysis,
    entry: SproutsItemCatalogEntry,
    *,
    seen_in_other_receipt: bool,
) -> float:
    score = float(entry.count)
    if seen_in_other_receipt:
        score += 20
    if entry.category == "PRODUCE":
        score += 15
    if "BANANA" in entry.product_text.upper():
        score += 15
    if len(analysis.receipt.get("words", [])) <= 185:
        score += 3
    score += max(0, 8 - len(entry.product_tokens))
    return score


def _observed_item_evidence(
    plan: SproutsAddItemPlan,
    analyses: list[SproutsArithmeticAnalysis],
) -> dict[str, Any]:
    base_key = _receipt_key(plan.analysis.receipt)
    product_sources = sorted(plan.item.source_receipt_keys)
    category_item_sources = sorted(
        {
            _receipt_key(analysis.receipt)
            for analysis in analyses
            if any(
                item.category == plan.item.category
                for item in analysis.line_items
            )
        }
    )
    category_heading_sources = sorted(
        {
            _receipt_key(analysis.receipt)
            for analysis in analyses
            if plan.item.category in analysis.category_sequence
        }
    )
    return {
        "base_receipt_key": base_key,
        "product_seen_in_receipts": product_sources[:8],
        "product_seen_outside_base": [
            source for source in product_sources if source != base_key
        ][:8],
        "product_observed_count": plan.item.count,
        "category": plan.item.category,
        "category_seen_in_receipts": category_item_sources[:8],
        "category_seen_count": len(category_item_sources),
        "category_heading_seen_count": len(category_heading_sources),
        "base_receipt_has_category": base_key in category_item_sources,
    }


def _is_catalog_item(item: SproutsLineItem) -> bool:
    product_text = _normalize_product_text(item.product_text)
    if not product_text or item.amount <= Decimal("0.00"):
        return False
    if item.taxable:
        return False
    if item.category == "CRV":
        return False
    if product_text.replace(".", "").isdigit():
        return False
    tokens = product_text.split()
    if len(tokens) > 6:
        return False
    if len(tokens) == 1 and len(tokens[0]) <= 1:
        return False
    return True


def _score_structure_similarity(
    receipt: dict[str, Any],
    analyses: list[SproutsArithmeticAnalysis],
) -> dict[str, Any]:
    candidate = _analyze_arithmetic_receipt(receipt)
    if candidate is None or not analyses:
        return {
            "score": 0.0,
            "nearest_real_receipt_key": None,
            "components": {},
        }

    candidate_key = _receipt_key(receipt)
    comparison_pool = [
        analysis
        for analysis in analyses
        if _receipt_key(analysis.receipt) != candidate_key
    ] or analyses

    scored = [
        (
            _structure_similarity_components(candidate, analysis),
            analysis,
        )
        for analysis in comparison_pool
    ]
    best_components, best_analysis = max(
        scored,
        key=lambda item: _weighted_structure_score(item[0]),
    )
    score = _weighted_structure_score(best_components)
    baseline_comparison = compare_structure_to_real_baseline(
        round(score, 3),
        _build_real_structure_baseline(analyses),
    )
    candidate_signature = _receipt_signature(candidate)
    nearest_signature = _receipt_signature(best_analysis)
    nearest_real_evidence = build_nearest_real_structure_evidence(
        best_components,
        candidate_signature,
        nearest_signature,
    )
    return {
        "score": round(score, 3),
        "nearest_real_receipt_key": _receipt_key(best_analysis.receipt),
        "components": {
            key: round(value, 3) for key, value in best_components.items()
        },
        "candidate_signature": candidate_signature,
        "nearest_signature": nearest_signature,
        "real_baseline_comparison": baseline_comparison,
        **nearest_real_evidence,
    }


def _structure_similarity_components(
    candidate: SproutsArithmeticAnalysis,
    real: SproutsArithmeticAnalysis,
) -> dict[str, float]:
    candidate_price_x = _label_x_p50(candidate.receipt, "LINE_TOTAL")
    real_price_x = _label_x_p50(real.receipt, "LINE_TOTAL")
    if candidate_price_x is None or real_price_x is None:
        price_column_score = 0.0
    else:
        price_column_score = _distance_score(
            abs(candidate_price_x - real_price_x),
            scale=250,
        )

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
            len(candidate.line_items),
            len(real.line_items),
        ),
        "token_count": _ratio_close(
            len(candidate.receipt.get("words", [])),
            len(real.receipt.get("words", [])),
        ),
        "price_column": price_column_score,
        "line_step": _distance_score(
            abs(
                _line_step(candidate.line_items) - _line_step(real.line_items)
            ),
            scale=40,
        ),
    }


def _weighted_structure_score(components: dict[str, float]) -> float:
    weights = {
        "category_sequence": 0.25,
        "category_set": 0.15,
        "item_count": 0.18,
        "token_count": 0.12,
        "price_column": 0.18,
        "line_step": 0.12,
    }
    return sum(
        components.get(key, 0.0) * weight for key, weight in weights.items()
    )


def _build_real_structure_baseline(
    analyses: list[SproutsArithmeticAnalysis],
) -> dict[str, Any]:
    valid = [analysis for analysis in analyses if analysis.line_items]
    component_values: dict[str, list[float]] = defaultdict(list)
    scores: list[float] = []

    for left_index, left in enumerate(valid):
        for right in valid[left_index + 1 :]:
            components = _structure_similarity_components(left, right)
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


def _bounded_score(value: Any) -> float:
    return max(0.0, min(1.0, _safe_float(value, 0.0)))


def _receipt_signature(analysis: SproutsArithmeticAnalysis) -> dict[str, Any]:
    return {
        "token_count": len(analysis.receipt.get("words", [])),
        "line_count": len(analysis.receipt.get("lines", [])),
        "line_item_count": len(analysis.line_items),
        "category_sequence": analysis.category_sequence,
        "line_step": _line_step(analysis.line_items),
        "line_total_x_p50": _label_x_p50(analysis.receipt, "LINE_TOTAL"),
    }


def _summarize_category_patterns(
    analyses: list[SproutsArithmeticAnalysis],
) -> dict[str, Any]:
    if not analyses:
        return {}

    heading_counts = Counter(
        heading.category
        for analysis in analyses
        for heading in analysis.section_headings
    )
    sequence_counts = Counter(
        tuple(analysis.category_sequence)
        for analysis in analyses
        if analysis.category_sequence
    )
    items_by_category: dict[str, Counter[str]] = defaultdict(Counter)
    for analysis in analyses:
        for item in analysis.line_items:
            category = item.category or UNKNOWN_CATEGORY
            items_by_category[category][item.product_text] += 1

    return {
        "heading_counts": dict(heading_counts.most_common()),
        "common_category_sequences": [
            {"sequence": list(sequence), "count": count}
            for sequence, count in sequence_counts.most_common(8)
        ],
        "top_items_by_category": {
            category: [
                {"product_text": product_text, "count": count}
                for product_text, count in counts.most_common(8)
            ]
            for category, counts in sorted(items_by_category.items())
        },
    }


def _line_category_heading(line: dict[str, Any]) -> str | None:
    tokens = [
        _normalize_heading_token(word.get("text"))
        for word in line.get("words", [])
    ]
    tokens = [token for token in tokens if token]
    if not tokens or len(tokens) > 2:
        return None
    text = " ".join(tokens)
    if text in SPROUTS_CATEGORY_HEADINGS:
        return text
    if len(tokens) == 1 and tokens[0] in SPROUTS_CATEGORY_HEADINGS:
        return tokens[0]
    return None


def _coalesce_category(first: str, second: str) -> str:
    if first and first != UNKNOWN_CATEGORY:
        return first
    if second and second != UNKNOWN_CATEGORY:
        return second
    return UNKNOWN_CATEGORY


def _normalize_heading_token(value: Any) -> str:
    return str(value or "").strip().upper().strip(":")


def _normalize_product_text(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


def _has_similar_product(
    product_text: str,
    existing_product_texts: list[str],
) -> bool:
    product_tokens = _product_identity_tokens(product_text)
    product_key = _normalize_product_text(product_text)
    for existing in existing_product_texts:
        existing_key = _normalize_product_text(existing)
        if existing_key == product_key:
            return True
        existing_tokens = _product_identity_tokens(existing)
        if (
            product_tokens
            and existing_tokens
            and product_tokens & existing_tokens
        ):
            return True
    return False


def _product_identity_tokens(product_text: str) -> set[str]:
    stop_words = {
        "BLACK",
        "BLUE",
        "BROWN",
        "EXTRA",
        "FRESH",
        "GREEN",
        "ORG",
        "ORGANIC",
        "RAW",
        "RED",
        "SPROUTS",
        "THE",
        "WHITE",
        "WHOLE",
        "YELLOW",
    }
    tokens = set()
    for token in _normalize_product_text(product_text).split():
        token = token.strip("-_/")
        if not token or token in stop_words or len(token) <= 2:
            continue
        if token.endswith("S") and len(token) > 4:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _median_money(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0.00")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return _money(ordered[mid])
    return _money((ordered[mid - 1] + ordered[mid]) / Decimal("2"))


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
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def _ratio_close(left: int, right: int) -> float:
    denominator = max(left, right, 1)
    return max(0.0, 1.0 - (abs(left - right) / denominator))


def _distance_score(distance: float, *, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return max(0.0, 1.0 - min(distance / scale, 1.0))


def _label_x_p50(receipt: dict[str, Any], label: str) -> float | None:
    xs = [
        _cx(word["bbox"])
        for line in receipt.get("lines", [])
        for word in line.get("words", [])
        if label in word.get("labels", [])
    ]
    summary = _summary(xs)
    return summary.p50 if summary else None


def _flatten_receipt_lines(
    lines: list[dict[str, Any]],
) -> tuple[list[str], list[list[int]], list[str]]:
    tokens: list[str] = []
    bboxes: list[list[int]] = []
    ner_tags: list[str] = []
    for line in lines:
        line_labels: list[str] = []
        for word in line.get("words", []):
            tokens.append(word["text"])
            bboxes.append(word["bbox"])
            line_labels.append(_first_label(word.get("labels", [])))
        ner_tags.extend(_bio_tags(line_labels))
    return tokens, bboxes, ner_tags


def _insert_line_sorted(
    receipt: dict[str, Any],
    line: dict[str, Any],
) -> None:
    lines = receipt.setdefault("lines", [])
    inserted_y = _line_y(line)
    insert_at = len(lines)
    for idx, existing in enumerate(lines):
        if _line_y(existing) < inserted_y:
            insert_at = idx
            break
    lines.insert(insert_at, line)
    _refresh_receipt_words(receipt)


def _apply_non_taxable_delta(
    receipt: dict[str, Any],
    analysis: SproutsArithmeticAnalysis,
    *,
    delta: Decimal,
) -> dict[str, Any]:
    old_subtotal = analysis.subtotal
    old_grand_total = analysis.grand_total
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

    _refresh_receipt_words(receipt)
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


def _shift_lines_below(
    receipt: dict[str, Any],
    removed_center_y: float,
    delta: int,
) -> None:
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 >= removed_center_y:
            continue
        for word in line.get("words", []):
            word["bbox"][1] = min(1000, word["bbox"][1] + delta)
            word["bbox"][3] = min(1000, word["bbox"][3] + delta)
        line["y"] = min(1.0, _line_y(line))
    _refresh_receipt_words(receipt)


def _can_shift_lines_below_down(
    receipt: dict[str, Any],
    anchor_y: float,
    delta: int,
) -> bool:
    bottom_margin = 8
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 >= anchor_y - 1:
            continue
        for word in line.get("words", []):
            if word["bbox"][1] - delta < bottom_margin:
                return False
    return True


def _shift_lines_below_down(
    receipt: dict[str, Any],
    anchor_y: float,
    delta: int,
) -> None:
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 >= anchor_y - 1:
            continue
        for word in line.get("words", []):
            word["bbox"][1] = max(0, word["bbox"][1] - delta)
            word["bbox"][3] = max(0, word["bbox"][3] - delta)
        line["y"] = max(0.0, _line_y(line))
    _refresh_receipt_words(receipt)


def _can_shift_lines_above_up(
    receipt: dict[str, Any],
    anchor_y: float,
    delta: int,
) -> bool:
    # Top of the receipt is high y, so "up" moves the block above the insertion
    # point toward y=1000. The fix succeeds only if the topmost word keeps a
    # margin from the ceiling, mirroring the bottom-margin check on the down
    # path so neither direction clips real content off the receipt.
    top_margin = 8
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 < anchor_y - 1:
            continue
        for word in line.get("words", []):
            if word["bbox"][3] + delta > 1000 - top_margin:
                return False
    return True


def _shift_lines_above_up(
    receipt: dict[str, Any],
    anchor_y: float,
    delta: int,
) -> None:
    for line in receipt.get("lines", []):
        if _line_y(line) * 1000 < anchor_y - 1:
            continue
        for word in line.get("words", []):
            word["bbox"][1] = min(1000, word["bbox"][1] + delta)
            word["bbox"][3] = min(1000, word["bbox"][3] + delta)
        line["y"] = min(1.0, _line_y(line))
    _refresh_receipt_words(receipt)


def _refresh_receipt_words(receipt: dict[str, Any]) -> None:
    words = []
    for line in receipt.get("lines", []):
        words.extend(line.get("words", []))
    receipt["words"] = words


def _build_mutation_line(mutation: dict[str, Any]) -> dict[str, Any] | None:
    tokens = mutation.get("tokens") or []
    if not tokens:
        return None
    x0 = int(mutation["x"])
    y0 = int(mutation["y"])
    words = []
    cursor = x0
    for idx, token in enumerate(tokens, start=1):
        width = max(34, min(128, 10 + len(str(token)) * 9))
        words.append(
            {
                "text": str(token),
                "bbox": [
                    max(0, cursor),
                    max(0, y0),
                    min(1000, cursor + width),
                    min(1000, y0 + 24),
                ],
                "labels": [],
                "line_id": 10_000,
                "word_id": idx,
            }
        )
        cursor = min(1000, cursor + width + 10)
    return {"line_id": 10_000, "y": y0 / 1000, "words": words}


def _nearest_open_y(receipt: dict[str, Any], mutation: dict[str, Any]) -> int:
    desired = int(mutation["y"])
    for delta in (0, -12, 12, -24, 24, -36, 36, -48, 48, -60, 60):
        candidate_y = max(0, min(976, desired + delta))
        if not _mutation_collides(receipt, mutation, candidate_y):
            return candidate_y
    return max(0, min(976, desired))


def _mutation_collides(
    receipt: dict[str, Any],
    mutation: dict[str, Any],
    y0: int,
) -> bool:
    proposed = _mutation_boxes(mutation, y0)
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


def _mutation_boxes(mutation: dict[str, Any], y0: int) -> list[list[int]]:
    boxes: list[list[int]] = []
    cursor = int(mutation["x"])
    for token in mutation.get("tokens") or []:
        width = max(34, min(128, 10 + len(str(token)) * 9))
        boxes.append(
            [
                max(0, cursor),
                max(0, y0),
                min(1000, cursor + width),
                min(1000, y0 + 24),
            ]
        )
        cursor = min(1000, cursor + width + 10)
    return boxes


def _boxes_overlap(a: list[int], b: list[int], *, padding: int = 0) -> bool:
    return not (
        a[2] + padding <= b[0]
        or a[0] - padding >= b[2]
        or a[3] + padding <= b[1]
        or a[1] - padding >= b[3]
    )


def _mutation_for_label(
    predicted_label: str,
    parameters: SproutsReceiptParameters,
) -> dict[str, Any] | None:
    if predicted_label == "ADDRESS_LINE":
        slot = parameters.slots["address_block"]
        x = _anchor(slot.x, default=430)
        y = _upper_anchor(slot.y, default=890, offset=-50)
        return {
            "name": "address_zone_loyalty_hard_negative",
            "predicted_label": predicted_label,
            "tokens": ["LOCAL", "FAVORITES"],
            "x": max(60, x - 80),
            "y": y,
            "target_zone": {"slot": "address_block", "x": x, "y": y},
        }
    if predicted_label == "DISCOUNT":
        product_slot = parameters.slots["product_column"]
        y = _anchor(product_slot.y, default=620)
        return {
            "name": "item_band_discount_lookalike_hard_negative",
            "predicted_label": predicted_label,
            "tokens": ["WEEKLY", "SPECIAL"],
            "x": 90,
            "y": y,
            "target_zone": {"slot": "product_column", "x": 90, "y": y},
        }
    if predicted_label == "GRAND_TOTAL":
        slot = parameters.slots["grand_total_column"]
        x = _anchor(slot.x, default=820)
        y = _anchor(slot.y, default=650)
        return {
            "name": "total_zone_change_amount_hard_negative",
            "predicted_label": predicted_label,
            "tokens": ["CHANGE", "0.00"],
            "x": max(580, x - 120),
            "y": max(0, y - 34),
            "target_zone": {"slot": "grand_total_column", "x": x, "y": y},
        }
    return None


def _choose_base_receipt(
    receipts: list[dict[str, Any]],
    *,
    used: int,
) -> dict[str, Any]:
    preferred = [
        receipt for receipt in receipts if len(receipt["words"]) <= 185
    ]
    pool = preferred or receipts
    return pool[min(used, len(pool) - 1)]


def _matching_line_total_row(
    product_row: dict[str, Any],
    line_total_rows: list[dict[str, Any]],
    used_total_lines: set[int],
) -> dict[str, Any] | None:
    same_line = [
        row
        for row in line_total_rows
        if row["line_index"] == product_row["line_index"]
        and row["line_index"] not in used_total_lines
    ]
    if same_line:
        return same_line[0]

    nearby = [
        row
        for row in line_total_rows
        if row["line_index"] not in used_total_lines
        and abs(row["center_y"] - product_row["center_y"]) <= 14
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


def _line_step(items: list[SproutsLineItem]) -> int:
    centers = sorted({round(item.center_y, 1) for item in items}, reverse=True)
    gaps = [
        abs(centers[idx] - centers[idx + 1])
        for idx in range(len(centers) - 1)
        if abs(centers[idx] - centers[idx + 1]) >= 8
    ]
    if not gaps:
        return 26
    return max(18, min(42, int(round(statistics.median(gaps)))))


def _line_is_taxable(*lines: dict[str, Any]) -> bool:
    return any(
        str(word.get("text") or "").upper().endswith("T")
        for line in lines
        for word in line.get("words", [])
    )


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
    if text.strip().startswith("$"):
        return f"${formatted}"
    if text.strip().upper().startswith("USD$"):
        return f"USD${formatted}"
    return formatted


def _right_align_money_box(word: dict[str, Any]) -> None:
    bbox = word.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return
    width = _token_width(str(word.get("text") or ""))
    right = bbox[2]
    bbox[0] = max(0, right - width)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_sum(values: Any) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        total += value
    return _money(total)


def _token_width(token: str) -> int:
    return max(34, min(140, 10 + len(str(token)) * 9))


def _examples_for_labels(
    receipts: list[dict[str, Any]],
    labels: list[str],
) -> list[str]:
    wanted = set(labels)
    return _dedupe(
        word["text"]
        for receipt in receipts
        for word in receipt["words"]
        if set(word["labels"]) & wanted
    )


def _summary(values: list[float | int]) -> GeometrySummary | None:
    numeric = sorted(float(value) for value in values)
    if not numeric:
        return None
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


def _anchor(summary: GeometrySummary | None, *, default: int) -> int:
    if summary is None:
        return default
    return int(round(summary.p50))


def _upper_anchor(
    summary: GeometrySummary | None,
    *,
    default: int,
    offset: int = 0,
) -> int:
    if summary is None:
        return default
    return max(0, min(1000, int(round(summary.p90 + offset))))


def _line_y(line: dict[str, Any]) -> float:
    words = line.get("words") or []
    if words:
        return statistics.median(_cy(word["bbox"]) / 1000 for word in words)
    return _safe_float(line.get("y"), 0.5)


def _cx(bbox: list[int]) -> float:
    return (bbox[0] + bbox[2]) / 2


def _cy(bbox: list[int]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _receipt_key(receipt: dict[str, Any]) -> str:
    image_id = str(receipt.get("image_id") or "unknown")
    receipt_num = receipt.get("receipt_num")
    if receipt_num is None:
        raw_receipt_id = str(receipt.get("receipt_id") or "00001")
        receipt_num = raw_receipt_id.rsplit("_", maxsplit=1)[-1]
    try:
        receipt_suffix = f"{int(receipt_num):05d}"
    except (TypeError, ValueError):
        receipt_suffix = str(receipt_num)
    return f"{image_id}#{receipt_suffix}"


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
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
    return "".join(slug).strip("-")[:120] or "sprouts-synthetic"
