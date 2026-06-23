"""Line item pattern discovery for receipts.

This module provides functions to discover line item patterns from receipt data
using LLM analysis. It can be used both locally for development and in Lambda.

The hybrid approach combines:
1. Raw receipt structure (lines with y-positions and words)
2. Chroma-enriched validated label examples from the same merchant

Usage:
    from receipt_agent.agents.label_evaluator.pattern_discovery import (
        build_receipt_structure,
        build_discovery_prompt,
        discover_patterns_with_llm,
        query_label_examples_from_chroma,
    )

    # Fetch receipt data
    receipts_data = build_receipt_structure(dynamo_client, "Sprouts Farmers Market")

    # Optionally get Chroma-validated examples
    label_examples = query_label_examples_from_chroma(
        chroma_client, "Sprouts Farmers Market"
    )

    # Build prompt with both sources
    prompt = build_discovery_prompt(
        "Sprouts Farmers Market",
        receipts_data,
        label_examples=label_examples,
    )

    # Call LLM
    patterns = discover_patterns_with_llm(prompt)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from receipt_agent.prompts.structured_outputs import (
    PatternDiscoveryResponse,
    extract_json_from_response,
)
from receipt_agent.utils.chroma_types import extract_query_metadata_rows
from receipt_agent.utils.llm_factory import (
    DEFAULT_OPENROUTER_MODEL,
    paid_llm_calls_disabled,
    resolve_openrouter_model,
)
from receipt_agent.utils.label_metadata import parse_labels_from_metadata
from receipt_agent.utils.structured_output import (
    get_structured_output_settings,
)

logger = logging.getLogger(__name__)


# Labels relevant for line item pattern discovery
LINE_ITEM_LABELS = [
    "PRODUCT_NAME",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "QUANTITY",
    "DISCOUNT",
    "TAX",
]


class DynamoClientProtocol(Protocol):
    """Protocol for DynamoDB client - allows duck typing."""

    def get_receipt_places_by_merchant(
        self, merchant_name: str, limit: int = 3
    ) -> tuple[list, Any]: ...

    def list_receipt_words_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list | tuple[list, Any]: ...

    def list_receipt_word_labels_for_receipt(
        self, image_id: str, receipt_id: int
    ) -> tuple[list, Any]: ...


@dataclass
class LabelExample:
    """A validated label example from ChromaDB."""

    word_text: str
    label: str
    x_position: float
    y_position: float
    left_neighbor: str
    right_neighbor: str


@dataclass
class LabelExamples:
    """Collection of validated label examples by label type."""

    examples_by_label: dict[str, list[LabelExample]] = field(
        default_factory=dict
    )
    merchant_name: str = ""
    total_examples: int = 0

    def add_example(self, label: str, example: LabelExample) -> None:
        """Add an example to the collection."""
        if label not in self.examples_by_label:
            self.examples_by_label[label] = []
        self.examples_by_label[label].append(example)
        self.total_examples += 1

    def get_examples(self, label: str, limit: int = 5) -> list[LabelExample]:
        """Get examples for a specific label."""
        return self.examples_by_label.get(label, [])[:limit]

    def format_for_prompt(self) -> str:
        """Format examples for inclusion in LLM prompt."""
        if not self.examples_by_label:
            return ""

        lines = [f'VALIDATED LABEL EXAMPLES FROM "{self.merchant_name}":']
        for label in LINE_ITEM_LABELS:
            examples = self.get_examples(label, limit=5)
            if examples:
                example_strs = []
                for ex in examples:
                    context = f"[{ex.left_neighbor}] {ex.word_text} [{ex.right_neighbor}]"
                    example_strs.append(
                        f'"{ex.word_text}" at x={ex.x_position:.2f} ({context})'
                    )
                lines.append(f"  {label}: {', '.join(example_strs)}")
            else:
                lines.append(f"  {label}: (no validated examples found)")

        return "\n".join(lines)


@dataclass
class SimilarMerchantExample:
    """Validated Chroma evidence from a different merchant."""

    merchant_name: str
    label: str
    word_text: str
    x_position: float
    y_position: float
    left_neighbor: str
    right_neighbor: str
    query: str
    similarity: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant_name": self.merchant_name,
            "label": self.label,
            "word_text": self.word_text,
            "x_position": self.x_position,
            "y_position": self.y_position,
            "left_neighbor": self.left_neighbor,
            "right_neighbor": self.right_neighbor,
            "query": self.query,
            "similarity": self.similarity,
        }


BACKGROUND_LABEL = "O"

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

DATE_TIME_LABELS = {"DATE", "TIME", "STORE_HOURS"}


@dataclass
class ConfusionPairInput:
    """One off-diagonal entry from a model confusion matrix."""

    actual_label: str
    predicted_label: str
    count: int
    actual_total: int = 0

    @property
    def share(self) -> float:
        """Share of the actual-label row that landed in this confusion."""
        if self.actual_total <= 0:
            return 0.0
        return self.count / self.actual_total


@dataclass
class HeatmapCell:
    """A compact receipt-layout heatmap cell for a label family."""

    label: str
    x_zone: str
    y_band: str
    count: int
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "label": self.label,
            "x_zone": self.x_zone,
            "y_band": self.y_band,
            "count": self.count,
            "examples": self.examples,
        }


@dataclass
class ConfusionPatternTarget:
    """LLM-ready target derived from a confusion pair and receipt heatmap."""

    actual_label: str
    predicted_label: str
    count: int
    share: float
    error_kind: str
    pattern_family: str
    heatmap_label: str
    heatmap_cells: list[HeatmapCell]
    synthetic_receipt_brief: str
    llm_questions: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "actual_label": self.actual_label,
            "predicted_label": self.predicted_label,
            "count": self.count,
            "share": self.share,
            "error_kind": self.error_kind,
            "pattern_family": self.pattern_family,
            "heatmap_label": self.heatmap_label,
            "heatmap_cells": [cell.to_dict() for cell in self.heatmap_cells],
            "synthetic_receipt_brief": self.synthetic_receipt_brief,
            "llm_questions": self.llm_questions,
        }


@dataclass
class SyntheticReceiptRecipe:
    """One targeted receipt augmentation recipe derived from model errors."""

    recipe_id: str
    actual_label: str
    predicted_label: str
    error_kind: str
    objective: str
    merchant_scope: str
    target_zone: dict[str, str]
    source_examples: list[str]
    retrieval_queries: list[str]
    layout_constraints: list[str]
    mutation_steps: list[str]
    expected_label_effect: str
    safeguards: list[str]
    llm_rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        result = {
            "recipe_id": self.recipe_id,
            "actual_label": self.actual_label,
            "predicted_label": self.predicted_label,
            "error_kind": self.error_kind,
            "objective": self.objective,
            "merchant_scope": self.merchant_scope,
            "target_zone": self.target_zone,
            "source_examples": self.source_examples,
            "retrieval_queries": self.retrieval_queries,
            "layout_constraints": self.layout_constraints,
            "mutation_steps": self.mutation_steps,
            "expected_label_effect": self.expected_label_effect,
            "safeguards": self.safeguards,
        }
        if self.llm_rationale:
            result["llm_rationale"] = self.llm_rationale
        return result


@dataclass
class SyntheticReceiptPlan:
    """Machine-readable bridge from confusion targets to synthetic receipts."""

    merchant_name: str
    source_receipt_count: int
    confusion_target_count: int
    recipes: list[SyntheticReceiptRecipe]
    synthetic_receipt_guidance: list[str]
    similar_merchant_mining: dict[str, Any]
    metric_guardrails: list[str]
    overtraining_guards: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "merchant_name": self.merchant_name,
            "source_receipt_count": self.source_receipt_count,
            "confusion_target_count": self.confusion_target_count,
            "recipes": [recipe.to_dict() for recipe in self.recipes],
            "synthetic_receipt_guidance": self.synthetic_receipt_guidance,
            "similar_merchant_mining": self.similar_merchant_mining,
            "metric_guardrails": self.metric_guardrails,
            "overtraining_guards": self.overtraining_guards,
        }


@dataclass
class SyntheticReceiptCandidate:
    """One LayoutLM-style synthetic training candidate."""

    candidate_id: str
    recipe_id: str
    merchant_name: str
    tokens: list[str]
    bboxes: list[list[int]]
    ner_tags: list[str]
    receipt_key: str
    image_id: str
    train_only: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to the raw example shape expected by the training loader."""
        return {
            "candidate_id": self.candidate_id,
            "recipe_id": self.recipe_id,
            "merchant_name": self.merchant_name,
            "tokens": self.tokens,
            "bboxes": self.bboxes,
            "ner_tags": self.ner_tags,
            "receipt_key": self.receipt_key,
            "image_id": self.image_id,
            "train_only": self.train_only,
            "metadata": self.metadata,
        }


def normalize_confusion_label(label: str | None) -> str:
    """Normalize BIO prefixes and display labels used by model metrics."""
    if not label:
        return BACKGROUND_LABEL

    normalized = str(label).strip().upper().replace(" ", "_")
    if normalized in {"NONE", "NULL", "BACKGROUND"}:
        return BACKGROUND_LABEL
    if normalized.startswith("B-") or normalized.startswith("I-"):
        normalized = normalized[2:]
    if normalized == "ADDRESS":
        return "ADDRESS_LINE"
    if normalized == "MERCHANT":
        return "MERCHANT_NAME"
    if normalized == "PHONE":
        return "PHONE_NUMBER"
    return normalized


def _confusion_error_kind(actual_label: str, predicted_label: str) -> str:
    """Classify the role of a confusion pair for synthesis."""
    if actual_label == predicted_label:
        return "correct"
    if actual_label == BACKGROUND_LABEL:
        return "false_positive"
    if predicted_label == BACKGROUND_LABEL:
        return "missed_entity"
    return "label_swap"


def _pattern_family(*labels: str) -> str:
    """Map one or more labels to the receipt region the LLM should inspect."""
    normalized = {normalize_confusion_label(label) for label in labels}
    if "MERCHANT_NAME" in normalized:
        return "merchant header"
    if "ADDRESS_LINE" in normalized:
        return "address block"
    if normalized & AMOUNT_LABELS:
        return "price row"
    if normalized & DATE_TIME_LABELS:
        return "date/time block"
    if normalized & {"PHONE_NUMBER", "WEBSITE"}:
        return "contact footer"
    if normalized & {"PRODUCT_NAME", "QUANTITY"}:
        return "line item grid"
    return "layout neighborhood"


def _x_zone(x_position: float) -> str:
    """Convert a normalized x coordinate into a coarse receipt zone."""
    x = max(0.0, min(1.0, x_position))
    if x < 0.33:
        return "left"
    if x < 0.67:
        return "center"
    return "right"


def _y_band(y_position: float) -> str:
    """Convert a normalized y coordinate into a coarse receipt band."""
    y = max(0.0, min(1.0, y_position))
    if y >= 0.67:
        return "top"
    if y >= 0.33:
        return "middle"
    return "bottom"


def build_top_confusion_pairs(
    labels: list[str],
    matrix: list[list[int | float]],
    limit: int = 3,
) -> list[ConfusionPairInput]:
    """Rank off-diagonal confusion-matrix pairs by count and row share.

    Args:
        labels: Matrix labels where row is actual and column is predicted.
        matrix: Confusion matrix recorded by training/evaluation.
        limit: Maximum number of pairs to return.

    Returns:
        Top off-diagonal confusion pairs.
    """
    pairs: list[ConfusionPairInput] = []

    for row_idx, actual_label_raw in enumerate(labels):
        row = matrix[row_idx] if row_idx < len(matrix) else []
        actual_total = int(sum(value or 0 for value in row))
        if actual_total <= 0:
            continue

        for col_idx, value in enumerate(row):
            if row_idx == col_idx or not value or value <= 0:
                continue
            if col_idx >= len(labels):
                continue

            pairs.append(
                ConfusionPairInput(
                    actual_label=normalize_confusion_label(actual_label_raw),
                    predicted_label=normalize_confusion_label(labels[col_idx]),
                    count=int(value),
                    actual_total=actual_total,
                )
            )

    return sorted(pairs, key=lambda pair: (-pair.count, -pair.share))[
        : max(0, limit)
    ]


def build_label_heatmap(
    receipts_data: list[dict],
    *,
    max_examples_per_cell: int = 4,
) -> dict[str, list[HeatmapCell]]:
    """Compute coarse x/y label heatmaps from structured receipt data."""
    cells: dict[tuple[str, str, str], HeatmapCell] = {}

    for receipt in receipts_data:
        for line in receipt.get("lines", []):
            y_value = float(line.get("y", 0.5) or 0.5)
            y_band = _y_band(y_value)
            for word in line.get("words", []):
                x_value = float(word.get("x", 0.5) or 0.5)
                x_zone = _x_zone(x_value)
                word_labels = word.get("labels") or [BACKGROUND_LABEL]
                text = str(word.get("text", "")).strip()

                for raw_label in word_labels:
                    label = normalize_confusion_label(raw_label)
                    key = (label, x_zone, y_band)
                    if key not in cells:
                        cells[key] = HeatmapCell(
                            label=label,
                            x_zone=x_zone,
                            y_band=y_band,
                            count=0,
                        )
                    cell = cells[key]
                    cell.count += 1
                    if text and len(cell.examples) < max_examples_per_cell:
                        cell.examples.append(text)

    heatmap: dict[str, list[HeatmapCell]] = {}
    for cell in cells.values():
        heatmap.setdefault(cell.label, []).append(cell)

    for label_cells in heatmap.values():
        label_cells.sort(
            key=lambda cell: (
                -cell.count,
                {"top": 0, "middle": 1, "bottom": 2}[cell.y_band],
                {"left": 0, "center": 1, "right": 2}[cell.x_zone],
            )
        )

    return heatmap


def _top_heatmap_cells(
    heatmap: dict[str, list[HeatmapCell]],
    labels: list[str],
    limit: int = 3,
) -> list[HeatmapCell]:
    """Return the most useful heatmap cells for the requested labels."""
    selected: list[HeatmapCell] = []
    seen: set[tuple[str, str, str]] = set()
    for label in labels:
        for cell in heatmap.get(label, []):
            key = (cell.label, cell.x_zone, cell.y_band)
            if key in seen:
                continue
            seen.add(key)
            selected.append(cell)

    selected.sort(key=lambda cell: -cell.count)
    return selected[:limit]


def _format_heatmap_summary(cells: list[HeatmapCell]) -> str:
    """Compact heatmap cells into human-readable synthesis constraints."""
    if not cells:
        return "no observed cells yet"

    parts = []
    for cell in cells:
        examples = f" examples={cell.examples[:3]}" if cell.examples else ""
        parts.append(
            f"{cell.label}@{cell.y_band}/{cell.x_zone} n={cell.count}{examples}"
        )
    return "; ".join(parts)


def _build_synthetic_receipt_brief(
    pair: ConfusionPairInput,
    heatmap_cells: list[HeatmapCell],
) -> str:
    """Describe how this confusion should influence synthetic receipts."""
    heatmap_summary = _format_heatmap_summary(heatmap_cells)
    family = _pattern_family(pair.actual_label, pair.predicted_label)
    error_kind = _confusion_error_kind(pair.actual_label, pair.predicted_label)

    if error_kind == "missed_entity":
        return (
            f"Synthesize more {family} receipts where {pair.actual_label} appears "
            f"in observed zones ({heatmap_summary}) with varied neighbors so the "
            "model stops dropping it into background."
        )

    if error_kind == "false_positive":
        return (
            f"Synthesize hard-negative {family} receipts with background tokens "
            f"near {pair.predicted_label} zones ({heatmap_summary}) that should "
            "remain unlabeled."
        )

    return (
        f"Synthesize contrast-set {family} receipts where {pair.actual_label} and "
        f"{pair.predicted_label} co-occur in their usual zones ({heatmap_summary}) "
        "with clear layout cues distinguishing the two labels."
    )


def build_confusion_pattern_targets(
    labels: list[str],
    matrix: list[list[int | float]],
    receipts_data: list[dict] | None = None,
    limit: int = 3,
) -> list[ConfusionPatternTarget]:
    """Build LLM-ready pattern targets from top confusion pairs.

    This is the bridge from Dynamo-recorded model metrics to receipt synthesis:
    matrix errors identify *which* label relation is weak, and receipt heatmaps
    identify *where* synthetic examples should place the hard cases.
    """
    heatmap = build_label_heatmap(receipts_data or [])
    pairs = build_top_confusion_pairs(labels, matrix, limit=limit)
    targets: list[ConfusionPatternTarget] = []

    for pair in pairs:
        error_kind = _confusion_error_kind(
            pair.actual_label, pair.predicted_label
        )
        if error_kind == "missed_entity":
            heatmap_labels = [pair.actual_label]
            heatmap_label = pair.actual_label
        elif error_kind == "false_positive":
            heatmap_labels = [pair.predicted_label]
            heatmap_label = pair.predicted_label
        else:
            heatmap_labels = [pair.actual_label, pair.predicted_label]
            heatmap_label = f"{pair.actual_label}+{pair.predicted_label}"

        cells = _top_heatmap_cells(heatmap, heatmap_labels)
        family = _pattern_family(pair.actual_label, pair.predicted_label)
        synthetic_brief = _build_synthetic_receipt_brief(pair, cells)
        questions = [
            (
                "Which merchant-specific words, prefixes, totals, or nearby "
                f"tokens make this {family} pattern ambiguous?"
            ),
            (
                "Which heatmap zones should synthetic receipts preserve, and "
                "which zones should receive hard-negative lookalikes?"
            ),
            (
                "What minimal receipt variants would improve this confusion "
                "without changing unrelated label distributions?"
            ),
        ]

        targets.append(
            ConfusionPatternTarget(
                actual_label=pair.actual_label,
                predicted_label=pair.predicted_label,
                count=pair.count,
                share=pair.share,
                error_kind=error_kind,
                pattern_family=family,
                heatmap_label=heatmap_label,
                heatmap_cells=cells,
                synthetic_receipt_brief=synthetic_brief,
                llm_questions=questions,
            )
        )

    return targets


def _target_from_dict(data: dict[str, Any]) -> ConfusionPatternTarget:
    """Convert a serialized target back to the dataclass shape."""
    cells = [
        HeatmapCell(
            label=normalize_confusion_label(cell.get("label")),
            x_zone=str(cell.get("x_zone", "center")),
            y_band=str(cell.get("y_band", "middle")),
            count=int(cell.get("count", 0)),
            examples=list(cell.get("examples") or []),
        )
        for cell in data.get("heatmap_cells", [])
        if isinstance(cell, dict)
    ]
    return ConfusionPatternTarget(
        actual_label=normalize_confusion_label(data.get("actual_label")),
        predicted_label=normalize_confusion_label(data.get("predicted_label")),
        count=int(data.get("count", 0)),
        share=float(data.get("share", 0.0)),
        error_kind=str(data.get("error_kind", "label_swap")),
        pattern_family=str(data.get("pattern_family", "layout neighborhood")),
        heatmap_label=str(data.get("heatmap_label", "")),
        heatmap_cells=cells,
        synthetic_receipt_brief=str(data.get("synthetic_receipt_brief", "")),
        llm_questions=list(data.get("llm_questions") or []),
    )


def format_confusion_targets_for_prompt(
    confusion_targets: list[ConfusionPatternTarget | dict[str, Any]],
) -> str:
    """Format confusion-driven synthesis targets for the LLM prompt."""
    if not confusion_targets:
        return ""

    normalized_targets = [
        (
            target
            if isinstance(target, ConfusionPatternTarget)
            else _target_from_dict(target)
        )
        for target in confusion_targets
    ]

    lines = [
        "CONFUSION-DRIVEN HEATMAP TARGETS:",
        "Use these model errors to mine receipt patterns and propose synthetic receipts.",
    ]

    for idx, target in enumerate(normalized_targets, start=1):
        heatmap_summary = _format_heatmap_summary(target.heatmap_cells)
        lines.extend(
            [
                (
                    f"{idx}. {target.actual_label} -> {target.predicted_label} "
                    f"({target.count} errors, {target.share:.1%} of row, "
                    f"{target.error_kind})"
                ),
                f"   Pattern family: {target.pattern_family}",
                f"   Heatmap: {heatmap_summary}",
                f"   Synthetic brief: {target.synthetic_receipt_brief}",
            ]
        )
        for question in target.llm_questions[:3]:
            lines.append(f"   LLM check: {question}")

    return "\n".join(lines)


def format_similar_merchant_examples_for_prompt(
    similar_merchant_examples: (
        list[SimilarMerchantExample | dict[str, Any]] | None
    ),
    *,
    limit: int = 12,
) -> str:
    """Format cross-merchant Chroma evidence for the discovery prompt."""
    evidence = _similar_evidence_dicts(similar_merchant_examples)
    if not evidence:
        return ""

    lines = ["SIMILAR-MERCHANT EVIDENCE FROM CHROMA:"]
    for item in evidence[: max(0, limit)]:
        merchant = str(item.get("merchant_name") or "unknown merchant")
        label = normalize_confusion_label(item.get("label"))
        word_text = str(item.get("word_text") or "").strip() or "<blank>"
        left = str(item.get("left_neighbor") or "<EDGE>")
        right = str(item.get("right_neighbor") or "<EDGE>")
        query = str(item.get("query") or "").strip()
        try:
            x_pos = float(item.get("x_position", 0.0))
        except (TypeError, ValueError):
            x_pos = 0.0
        try:
            y_pos = float(item.get("y_position", 0.0))
        except (TypeError, ValueError):
            y_pos = 0.0
        similarity = item.get("similarity")
        similarity_text = ""
        if isinstance(similarity, (int, float)):
            similarity_text = f", similarity={similarity:.2f}"
        query_text = f', query="{query}"' if query else ""
        lines.append(
            "  - "
            f'{merchant}: "{word_text}"[{label}] '
            f"at x={x_pos:.2f}, y={y_pos:.2f} "
            f"with context [{left}] {word_text} [{right}]"
            f"{query_text}{similarity_text}"
        )

    return "\n".join(lines)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    """Return non-empty values once, preserving first occurrence."""
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _recipe_slug(*parts: str) -> str:
    """Build a stable compact recipe identifier."""
    slug = "-".join(
        "".join(ch.lower() if ch.isalnum() else "-" for ch in str(part)).strip(
            "-"
        )
        for part in parts
        if part
    )
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:96] or "synthetic-receipt"


def _extract_label_examples(
    receipts_data: list[dict] | None,
    labels: list[str],
    *,
    limit: int = 6,
) -> list[str]:
    """Collect observed word examples for labels from receipt structures."""
    wanted = {normalize_confusion_label(label) for label in labels}
    examples: list[str] = []
    for receipt in receipts_data or []:
        for line in receipt.get("lines", []):
            for word in line.get("words", []):
                word_labels = word.get("labels") or []
                normalized_labels = {
                    normalize_confusion_label(label) for label in word_labels
                }
                if wanted & normalized_labels:
                    examples.append(str(word.get("text", "")).strip())
                    if len(_dedupe_preserving_order(examples)) >= limit:
                        return _dedupe_preserving_order(examples)[:limit]
    return _dedupe_preserving_order(examples)[:limit]


def _default_source_examples(
    merchant_name: str,
    target: ConfusionPatternTarget,
    receipts_data: list[dict] | None,
) -> list[str]:
    """Choose examples from heatmap cells, receipts, then merchant name fallback."""
    heatmap_examples = [
        example for cell in target.heatmap_cells for example in cell.examples
    ]
    label_examples = _extract_label_examples(
        receipts_data,
        [target.actual_label, target.predicted_label],
    )
    merchant_examples = merchant_name.split()
    return _dedupe_preserving_order(
        heatmap_examples + label_examples + merchant_examples
    )[:6]


def _labels_for_confusion_target(target: ConfusionPatternTarget) -> list[str]:
    """Return non-background labels relevant to a confusion target."""
    return [
        label
        for label in _dedupe_preserving_order(
            [
                target.actual_label,
                target.predicted_label,
                target.heatmap_label,
            ]
        )
        if label and label != BACKGROUND_LABEL
    ]


def _similar_evidence_dicts(
    similar_merchant_examples: (
        list[SimilarMerchantExample | dict[str, Any]] | None
    ),
) -> list[dict[str, Any]]:
    """Normalize similar-merchant evidence to JSON-safe dicts."""
    evidence: list[dict[str, Any]] = []
    for item in similar_merchant_examples or []:
        if isinstance(item, SimilarMerchantExample):
            evidence.append(item.to_dict())
        elif isinstance(item, dict):
            evidence.append(dict(item))
    return evidence


def _similar_source_examples(
    similar_merchant_examples: list[dict[str, Any]],
    target: ConfusionPatternTarget,
) -> list[str]:
    """Extract evidence tokens relevant to one target."""
    labels = set(_labels_for_confusion_target(target))
    examples = []
    for item in similar_merchant_examples:
        label = normalize_confusion_label(item.get("label"))
        if label not in labels:
            continue
        text = str(item.get("word_text") or "").strip()
        if text:
            examples.append(text)
    return _dedupe_preserving_order(examples)[:4]


def _llm_confusion_guidance(
    llm_patterns: dict[str, Any] | None,
    target: ConfusionPatternTarget,
) -> tuple[str | None, list[str]]:
    """Find matching LLM rationale/guidance for a confusion target."""
    if not isinstance(llm_patterns, dict):
        return None, []

    actual = normalize_confusion_label(target.actual_label)
    predicted = normalize_confusion_label(target.predicted_label)
    rationale_parts: list[str] = []

    for pattern in llm_patterns.get("confusion_patterns") or []:
        if not isinstance(pattern, dict):
            continue
        pattern_actual = normalize_confusion_label(pattern.get("actual_label"))
        pattern_predicted = normalize_confusion_label(
            pattern.get("predicted_label")
        )
        if pattern_actual != actual or pattern_predicted != predicted:
            continue
        for key in ("pattern", "heatmap_rationale"):
            if pattern.get(key):
                rationale_parts.append(str(pattern[key]))
        if pattern.get("synthetic_receipt_strategy"):
            rationale_parts.append(str(pattern["synthetic_receipt_strategy"]))

    guidance = [
        str(item)
        for item in (llm_patterns.get("synthetic_receipt_guidance") or [])
        if item
    ]
    return (
        " | ".join(_dedupe_preserving_order(rationale_parts)) or None,
        _dedupe_preserving_order(guidance),
    )


def _target_zone(
    cell: HeatmapCell | None, target: ConfusionPatternTarget
) -> dict[str, str]:
    """Return the primary target zone for a recipe."""
    if cell:
        return {
            "label": cell.label,
            "y_band": cell.y_band,
            "x_zone": cell.x_zone,
        }
    return {
        "label": target.heatmap_label or target.actual_label,
        "y_band": "observed",
        "x_zone": "observed",
    }


def _objective_for_target(
    target: ConfusionPatternTarget, merchant_name: str
) -> str:
    """Describe the training objective for one confusion target."""
    if target.error_kind == "missed_entity":
        return (
            f"Increase positive {target.actual_label} coverage for {merchant_name} "
            "without moving the merchant's normal receipt geometry."
        )
    if target.error_kind == "false_positive":
        return (
            f"Create hard-negative background text near {target.predicted_label} "
            "zones so the model stops over-labeling lookalikes."
        )
    return (
        f"Build a contrast set where {target.actual_label} and "
        f"{target.predicted_label} appear together with clearer spatial cues."
    )


def _expected_label_effect(target: ConfusionPatternTarget) -> str:
    """Describe the metric movement expected from a recipe."""
    if target.error_kind == "missed_entity":
        return f"Improve {target.actual_label} recall with minimal precision loss."
    if target.error_kind == "false_positive":
        return f"Improve {target.predicted_label} precision by adding O hard negatives."
    return (
        f"Reduce {target.actual_label}->{target.predicted_label} swaps while "
        "preserving per-label support balance."
    )


def _retrieval_queries(
    merchant_name: str,
    target: ConfusionPatternTarget,
    examples: list[str],
) -> list[str]:
    """Build retrieval queries for same/similar merchant pattern mining."""
    labels = _dedupe_preserving_order(
        [target.actual_label, target.predicted_label, target.heatmap_label]
    )
    base_queries = [
        f"{merchant_name} receipt {target.pattern_family}",
        f"{merchant_name} {' '.join(labels)}",
        f"similar merchant receipt {' '.join(labels)}",
    ]
    example_queries = [
        f"{example} {target.pattern_family}" for example in examples[:3]
    ]
    return _dedupe_preserving_order(base_queries + example_queries)


def _layout_constraints(
    target: ConfusionPatternTarget, cell: HeatmapCell | None
) -> list[str]:
    """Turn heatmap evidence into recipe constraints."""
    constraints = [
        "Keep receipt width, line ordering, and y-band proportions close to real receipts.",
        "Do not synthesize labels outside observed zones unless creating explicit hard negatives.",
    ]
    if cell:
        constraints.append(
            f"Place {cell.label} candidates in the {cell.y_band}/{cell.x_zone} "
            f"cell observed {cell.count} times."
        )
    else:
        constraints.append(
            "Use existing receipt examples to infer the placement before generating text."
        )
    if target.pattern_family == "merchant header":
        constraints.append(
            "Preserve header spacing, store-number lines, and loyalty/header distractors."
        )
    elif target.pattern_family == "price row":
        constraints.append(
            "Preserve right-aligned numeric columns and nearby subtotal/tax/total cues."
        )
    elif target.pattern_family == "address block":
        constraints.append(
            "Preserve street-number and city/state line breaks around the address block."
        )
    return constraints


def _mutation_steps(
    target: ConfusionPatternTarget,
    source_examples: list[str],
    llm_rationale: str | None,
) -> list[str]:
    """Build concrete synthesis steps for a downstream receipt generator."""
    examples = ", ".join(source_examples[:3]) or "observed receipt tokens"
    steps = [
        f"Start from a real same-merchant receipt template with {target.pattern_family} visible.",
        f"Keep observed anchor tokens ({examples}) in or near the target zone.",
    ]
    if target.error_kind == "false_positive":
        steps.append(
            f"Insert unlabeled O tokens that resemble {target.predicted_label} "
            "without copying exact labeled examples."
        )
    elif target.error_kind == "missed_entity":
        steps.append(
            f"Create labeled {target.actual_label} variants with abbreviations, "
            "line breaks, casing changes, and neighboring distractors."
        )
    else:
        steps.append(
            f"Place {target.actual_label} and {target.predicted_label} in the same "
            "receipt while varying spacing and separators between them."
        )
    if llm_rationale:
        steps.append(f"Apply LLM-discovered cue: {llm_rationale}")
    steps.append(
        "Emit word boxes and labels, then reject samples whose arithmetic or label counts drift."
    )
    return steps


def _merchant_scope(target: ConfusionPatternTarget) -> str:
    """Choose the recipe's mining scope."""
    if "MERCHANT_NAME" in {target.actual_label, target.predicted_label}:
        return "same_and_similar_merchant_headers"
    if target.pattern_family in {"price row", "line item grid"}:
        return "same_merchant_layout_with_similar_merchant_hard_negatives"
    return "same_merchant_first_then_similar_merchants"


def build_synthetic_receipt_plan(
    merchant_name: str,
    confusion_targets: list[ConfusionPatternTarget | dict[str, Any]],
    *,
    receipts_data: list[dict] | None = None,
    llm_patterns: dict[str, Any] | None = None,
    similar_merchant_examples: (
        list[SimilarMerchantExample | dict[str, Any]] | None
    ) = None,
    max_recipes_per_target: int = 2,
) -> SyntheticReceiptPlan:
    """Build a machine-readable synthetic-receipt augmentation plan.

    The plan is intentionally not the synthetic receipt itself. It is the next
    artifact between model metrics and a generator: each recipe names the
    confusion, the heatmap zone to preserve, retrieval queries for same/similar
    merchants, concrete mutations, expected metric movement, and overtraining
    guardrails.
    """
    targets = [
        (
            target
            if isinstance(target, ConfusionPatternTarget)
            else _target_from_dict(target)
        )
        for target in confusion_targets
    ]
    recipes: list[SyntheticReceiptRecipe] = []
    all_guidance: list[str] = []
    similar_evidence = _similar_evidence_dicts(similar_merchant_examples)

    for target_idx, target in enumerate(targets, start=1):
        llm_rationale, llm_guidance = _llm_confusion_guidance(
            llm_patterns, target
        )
        all_guidance.extend(llm_guidance)
        source_examples = _default_source_examples(
            merchant_name,
            target,
            receipts_data,
        )
        source_examples = _dedupe_preserving_order(
            source_examples
            + _similar_source_examples(similar_evidence, target)
        )[:8]
        recipe_cells = target.heatmap_cells[: max(1, max_recipes_per_target)]
        if not recipe_cells:
            recipe_cells = [None]  # type: ignore[list-item]

        for variant_idx, cell in enumerate(recipe_cells, start=1):
            zone = _target_zone(cell, target)
            recipe_id = _recipe_slug(
                merchant_name,
                str(target_idx),
                target.actual_label,
                target.predicted_label,
                str(variant_idx),
                zone.get("y_band", ""),
                zone.get("x_zone", ""),
            )
            recipes.append(
                SyntheticReceiptRecipe(
                    recipe_id=recipe_id,
                    actual_label=target.actual_label,
                    predicted_label=target.predicted_label,
                    error_kind=target.error_kind,
                    objective=_objective_for_target(target, merchant_name),
                    merchant_scope=_merchant_scope(target),
                    target_zone=zone,
                    source_examples=source_examples,
                    retrieval_queries=_retrieval_queries(
                        merchant_name,
                        target,
                        source_examples,
                    ),
                    layout_constraints=_layout_constraints(target, cell),
                    mutation_steps=_mutation_steps(
                        target,
                        source_examples,
                        llm_rationale,
                    ),
                    expected_label_effect=_expected_label_effect(target),
                    safeguards=[
                        "Keep validation and test sets real-only; never add synthetic samples there.",
                        "Cap synthetic samples to a minority of the next training batch.",
                        "Require post-training confusion-pair count to fall without worsening adjacent labels.",
                    ],
                    llm_rationale=llm_rationale,
                )
            )

    similar_merchant_queries = _dedupe_preserving_order(
        query
        for recipe in recipes
        for query in recipe.retrieval_queries
        if "similar merchant" in query
    )
    return SyntheticReceiptPlan(
        merchant_name=merchant_name,
        source_receipt_count=len(receipts_data or []),
        confusion_target_count=len(targets),
        recipes=recipes,
        synthetic_receipt_guidance=_dedupe_preserving_order(all_guidance),
        similar_merchant_mining={
            "strategy": (
                "Mine same-merchant receipts first, then query Chroma for similar "
                "merchant lines with matching labels to create hard-negative and "
                "contrast examples."
            ),
            "queries": similar_merchant_queries,
            "evidence": similar_evidence,
            "acceptance_criteria": [
                "Same x/y heatmap family as the target merchant",
                "Validated labels present for the target or predicted label",
                "Merchant identity differs when generating similar-merchant hard negatives",
            ],
        },
        metric_guardrails=[
            "Select targets from the best validation-F1 epoch, not the latest overfit epoch.",
            "Track val_f1, val_precision, val_recall, train_loss, eval_loss, and top confusion counts after augmentation.",
            "Treat rising eval_loss with falling train_loss as a stop signal unless the target confusion also improves.",
        ],
        overtraining_guards=[
            "Keep synthetic data below 20% of new training examples until real validation improves.",
            "Use merchant-stratified validation so added merchant-name examples do not leak into validation.",
            "Retire a recipe once its confusion pair is no longer in the top errors.",
        ],
    )


def _coerce_synthetic_plan(
    plan: SyntheticReceiptPlan | dict[str, Any],
) -> SyntheticReceiptPlan:
    """Convert serialized plan dictionaries back to dataclasses."""
    if isinstance(plan, SyntheticReceiptPlan):
        return plan

    recipes = []
    for recipe in plan.get("recipes", []):
        if not isinstance(recipe, dict):
            continue
        recipes.append(
            SyntheticReceiptRecipe(
                recipe_id=str(recipe.get("recipe_id", "")),
                actual_label=normalize_confusion_label(
                    recipe.get("actual_label")
                ),
                predicted_label=normalize_confusion_label(
                    recipe.get("predicted_label")
                ),
                error_kind=str(recipe.get("error_kind", "label_swap")),
                objective=str(recipe.get("objective", "")),
                merchant_scope=str(recipe.get("merchant_scope", "")),
                target_zone=dict(recipe.get("target_zone") or {}),
                source_examples=list(recipe.get("source_examples") or []),
                retrieval_queries=list(recipe.get("retrieval_queries") or []),
                layout_constraints=list(
                    recipe.get("layout_constraints") or []
                ),
                mutation_steps=list(recipe.get("mutation_steps") or []),
                expected_label_effect=str(
                    recipe.get("expected_label_effect", "")
                ),
                safeguards=list(recipe.get("safeguards") or []),
                llm_rationale=(
                    str(recipe.get("llm_rationale"))
                    if recipe.get("llm_rationale")
                    else None
                ),
            )
        )

    return SyntheticReceiptPlan(
        merchant_name=str(plan.get("merchant_name", "")),
        source_receipt_count=int(plan.get("source_receipt_count", 0)),
        confusion_target_count=int(
            plan.get("confusion_target_count", len(recipes))
        ),
        recipes=recipes,
        synthetic_receipt_guidance=list(
            plan.get("synthetic_receipt_guidance") or []
        ),
        similar_merchant_mining=dict(
            plan.get("similar_merchant_mining") or {}
        ),
        metric_guardrails=list(plan.get("metric_guardrails") or []),
        overtraining_guards=list(plan.get("overtraining_guards") or []),
    )


def _zone_anchor(zone: dict[str, str]) -> tuple[int, int]:
    """Map heatmap zone names to a stable LayoutLM bbox anchor."""
    x_zone = zone.get("x_zone", "center")
    y_band = zone.get("y_band", "middle")
    x_anchor = {"left": 90, "center": 390, "right": 720}.get(x_zone, 390)
    y_anchor = {"top": 80, "middle": 430, "bottom": 760}.get(y_band, 430)
    return x_anchor, y_anchor


def _line_boxes(tokens: list[str], x0: int, y0: int) -> list[list[int]]:
    """Create simple left-to-right word boxes in LayoutLM 0..1000 space."""
    boxes: list[list[int]] = []
    cursor = x0
    for token in tokens:
        width = max(36, min(140, 12 + len(token) * 10))
        boxes.append([cursor, y0, min(990, cursor + width), min(995, y0 + 28)])
        cursor = min(990, cursor + width + 12)
    return boxes


def _bio_tags(label: str, count: int) -> list[str]:
    """Build BIO tags for a contiguous token span."""
    normalized = normalize_confusion_label(label)
    if normalized == BACKGROUND_LABEL:
        return [BACKGROUND_LABEL] * count
    return [
        f"B-{normalized}",
        *[f"I-{normalized}" for _ in range(max(0, count - 1))],
    ]


def _merchant_tokens(merchant_name: str) -> list[str]:
    """Tokenize merchant name for synthetic examples."""
    return [part for part in merchant_name.replace("/", " ").split() if part][
        :4
    ] or ["MERCHANT"]


def _label_placeholder_tokens(
    label: str, source_examples: list[str]
) -> list[str]:
    """Choose deterministic token text for one label family."""
    normalized = normalize_confusion_label(label)
    examples = _dedupe_preserving_order(source_examples)
    if normalized == "MERCHANT_NAME":
        return examples[:2] or ["MERCHANT"]
    if normalized == "ADDRESS_LINE":
        return examples[:3] or ["123", "MAIN", "ST"]
    if normalized == "PHONE_NUMBER":
        return examples[:1] or ["555-0100"]
    if normalized == "WEBSITE":
        return examples[:1] or ["EXAMPLE.COM"]
    if normalized in DATE_TIME_LABELS:
        return examples[:1] or (
            ["10:24"] if normalized == "TIME" else ["06/22/26"]
        )
    if normalized in AMOUNT_LABELS or normalized == "AMOUNT":
        return examples[:1] or ["12.34"]
    if normalized == "PAYMENT_METHOD":
        return examples[:2] or ["VISA", "1234"]
    if normalized == "QUANTITY":
        return examples[:1] or ["2"]
    if normalized == "PRODUCT_NAME":
        return examples[:3] or ["ORGANIC", "MILK"]
    return examples[:2] or [normalized.replace("_", " ")]


def _hard_negative_tokens(recipe: SyntheticReceiptRecipe) -> list[str]:
    """Create unlabeled lookalike tokens for false-positive recipes."""
    examples = _dedupe_preserving_order(recipe.source_examples)
    base = examples[:2] or [recipe.predicted_label.replace("_", " ")]
    tokens = [part for example in base for part in str(example).split()]
    if recipe.predicted_label == "MERCHANT_NAME":
        return (tokens + ["REWARDS", "STORE", "#447"])[:4]
    if (
        recipe.predicted_label in AMOUNT_LABELS
        or recipe.predicted_label == "AMOUNT"
    ):
        return (tokens + ["SAVINGS", "POINTS"])[:3]
    return (tokens + ["REF", "INFO"])[:4]


def _append_line(
    tokens: list[str],
    bboxes: list[list[int]],
    tags: list[str],
    line_tokens: list[str],
    line_tags: list[str],
    *,
    x0: int,
    y0: int,
) -> None:
    """Append one synthetic line to token/bbox/tag buffers."""
    if not line_tokens:
        return
    tokens.extend(line_tokens)
    bboxes.extend(_line_boxes(line_tokens, x0, y0))
    tags.extend(line_tags)


def generate_synthetic_receipt_candidates(
    plan: SyntheticReceiptPlan | dict[str, Any],
    *,
    max_candidates: int = 12,
    receipts_data: list[dict[str, Any]] | None = None,
) -> list[SyntheticReceiptCandidate]:
    """Generate train-only LayoutLM examples from a synthetic receipt plan.

    These candidates are intentionally conservative. They are text+bbox+BIO
    examples derived from heatmap zones and LLM/deterministic recipe guidance,
    not raster images. The training loader can append them to the train split
    for LayoutLM v1 runs while validation remains real receipts only.
    """
    synthetic_plan = _coerce_synthetic_plan(plan)

    if receipts_data:
        try:
            from receipt_agent.agents.label_evaluator.merchant_synthesis import (
                generate_merchant_synthesis_candidates,
            )
            from receipt_agent.agents.label_evaluator.sprouts_parameterization import (
                generate_arithmetic_sprouts_candidates,
                generate_parameterized_sprouts_candidates,
                is_sprouts_merchant,
            )

            if is_sprouts_merchant(synthetic_plan.merchant_name):
                sprouts_candidate_limit = min(max_candidates, 5)
                sprouts_rows = generate_parameterized_sprouts_candidates(
                    synthetic_plan.to_dict(),
                    receipts_data,
                    max_candidates=min(sprouts_candidate_limit, 3),
                )
                remaining = sprouts_candidate_limit - len(sprouts_rows)
                if remaining > 0:
                    sprouts_rows.extend(
                        generate_arithmetic_sprouts_candidates(
                            receipts_data,
                            max_candidates=min(remaining, 2),
                        )
                    )
                if sprouts_rows:
                    return [
                        SyntheticReceiptCandidate(
                            candidate_id=str(row["candidate_id"]),
                            recipe_id=str(row["recipe_id"]),
                            merchant_name=str(row["merchant_name"]),
                            tokens=list(row["tokens"]),
                            bboxes=list(row["bboxes"]),
                            ner_tags=list(row["ner_tags"]),
                            receipt_key=str(row["receipt_key"]),
                            image_id=str(row["image_id"]),
                            train_only=bool(row.get("train_only", True)),
                            metadata=dict(row.get("metadata") or {}),
                        )
                        for row in sprouts_rows[:sprouts_candidate_limit]
                    ]

            merchant_rows = generate_merchant_synthesis_candidates(
                synthetic_plan.to_dict(),
                receipts_data,
                max_candidates=max_candidates,
            )
            if merchant_rows:
                return [
                    SyntheticReceiptCandidate(
                        candidate_id=str(row["candidate_id"]),
                        recipe_id=str(row["recipe_id"]),
                        merchant_name=str(row["merchant_name"]),
                        tokens=list(row["tokens"]),
                        bboxes=list(row["bboxes"]),
                        ner_tags=list(row["ner_tags"]),
                        receipt_key=str(row["receipt_key"]),
                        image_id=str(row["image_id"]),
                        train_only=bool(row.get("train_only", True)),
                        metadata=dict(row.get("metadata") or {}),
                    )
                    for row in merchant_rows
                ]
        except Exception:
            logger.exception(
                "Merchant parameterized synthesis failed; falling back to "
                "generic candidates"
            )

    candidates: list[SyntheticReceiptCandidate] = []

    for recipe_idx, recipe in enumerate(synthetic_plan.recipes):
        if len(candidates) >= max_candidates:
            break

        tokens: list[str] = []
        bboxes: list[list[int]] = []
        tags: list[str] = []
        merchant_tokens = _merchant_tokens(synthetic_plan.merchant_name)
        target_x, target_y = _zone_anchor(recipe.target_zone)

        _append_line(
            tokens,
            bboxes,
            tags,
            merchant_tokens,
            _bio_tags("MERCHANT_NAME", len(merchant_tokens)),
            x0=390,
            y0=60,
        )
        _append_line(
            tokens,
            bboxes,
            tags,
            ["STORE", "0421", "06/22/26"],
            ["O", "O", "B-DATE"],
            x0=90,
            y0=135,
        )

        if recipe.error_kind == "false_positive":
            target_tokens = _hard_negative_tokens(recipe)
            target_tags = ["O"] * len(target_tokens)
        elif recipe.error_kind == "missed_entity":
            target_tokens = _label_placeholder_tokens(
                recipe.actual_label,
                recipe.source_examples,
            )
            target_tags = _bio_tags(recipe.actual_label, len(target_tokens))
        else:
            actual_tokens = _label_placeholder_tokens(
                recipe.actual_label,
                recipe.source_examples,
            )
            predicted_tokens = _label_placeholder_tokens(
                recipe.predicted_label,
                recipe.source_examples,
            )
            target_tokens = actual_tokens + ["|"] + predicted_tokens
            target_tags = (
                _bio_tags(recipe.actual_label, len(actual_tokens))
                + ["O"]
                + _bio_tags(recipe.predicted_label, len(predicted_tokens))
            )

        _append_line(
            tokens,
            bboxes,
            tags,
            target_tokens,
            target_tags,
            x0=target_x,
            y0=target_y,
        )
        _append_line(
            tokens,
            bboxes,
            tags,
            ["ORGANIC", "MILK", "4.49"],
            ["B-PRODUCT_NAME", "I-PRODUCT_NAME", "B-LINE_TOTAL"],
            x0=90,
            y0=520,
        )
        _append_line(
            tokens,
            bboxes,
            tags,
            ["TOTAL", "4.49"],
            ["B-GRAND_TOTAL", "I-GRAND_TOTAL"],
            x0=650,
            y0=820,
        )

        candidate_id = _recipe_slug(
            "synthetic",
            synthetic_plan.merchant_name,
            recipe.recipe_id,
            str(recipe_idx + 1),
        )
        candidates.append(
            SyntheticReceiptCandidate(
                candidate_id=candidate_id,
                recipe_id=recipe.recipe_id,
                merchant_name=synthetic_plan.merchant_name,
                tokens=tokens,
                bboxes=bboxes,
                ner_tags=tags,
                image_id=f"synthetic-{candidate_id}",
                receipt_key=f"synthetic-{candidate_id}#00001",
                metadata={
                    "source": "confusion_heatmap_recipe",
                    "actual_label": recipe.actual_label,
                    "predicted_label": recipe.predicted_label,
                    "error_kind": recipe.error_kind,
                    "target_zone": recipe.target_zone,
                    "expected_label_effect": recipe.expected_label_effect,
                    "train_only_reason": (
                        "Synthetic examples target known model confusions and "
                        "must not enter validation."
                    ),
                },
            )
        )

    return candidates


@dataclass
class PatternDiscoveryConfig:
    """Configuration for pattern discovery."""

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    disable_paid_llm: bool = False
    max_receipts: int = 3
    max_lines_per_receipt: int = 80
    focus_on_line_items: bool = True  # Smart line selection

    @classmethod
    def from_env(cls) -> "PatternDiscoveryConfig":
        """Create config from environment variables.

        Checks for env vars in order of precedence:
        1. OPENROUTER_* (preferred)
        2. RECEIPT_AGENT_OPENROUTER_* (alternative prefix)
        """
        return cls(
            openrouter_api_key=(
                os.environ.get("OPENROUTER_API_KEY", "")
                or os.environ.get("RECEIPT_AGENT_OPENROUTER_API_KEY", "")
            ),
            openrouter_base_url=(
                os.environ.get("OPENROUTER_BASE_URL", "")
                or os.environ.get(
                    "RECEIPT_AGENT_OPENROUTER_BASE_URL",
                    "https://openrouter.ai/api/v1",
                )
            ),
            openrouter_model=(resolve_openrouter_model()),
            disable_paid_llm=paid_llm_calls_disabled(),
        )


def _is_strict_structured_output_enabled() -> bool:
    """Return whether strict structured output is enabled for LLM calls."""
    strict_enabled, _ = get_structured_output_settings(logger_instance=logger)
    return strict_enabled


def _set_additional_properties_false(schema_fragment: Any) -> None:
    """Recursively set additionalProperties=false on every object schema."""
    if isinstance(schema_fragment, dict):
        if schema_fragment.get("type") == "object":
            schema_fragment.setdefault("additionalProperties", False)
        for value in schema_fragment.values():
            _set_additional_properties_false(value)
        return

    if isinstance(schema_fragment, list):
        for item in schema_fragment:
            _set_additional_properties_false(item)


def _build_pattern_response_format() -> dict[str, Any]:
    """Build OpenAI-style JSON schema response_format for pattern discovery."""
    schema = PatternDiscoveryResponse.model_json_schema()
    _set_additional_properties_false(schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "pattern_discovery_response",
            "strict": True,
            "schema": schema,
        },
    }


# =============================================================================
# ChromaDB Query Functions
# =============================================================================


def query_label_examples_from_chroma(
    chroma_client: Any,
    merchant_name: str,
    embed_fn: Any,
    labels: list[str] | None = None,
    max_per_label: int = 10,
) -> LabelExamples:
    """Query ChromaDB for validated label examples from a specific merchant.

    Uses semantic search with label names to find examples.

    Args:
        chroma_client: ChromaDB client (DualChromaClient or similar)
        merchant_name: Name of the merchant to query
        embed_fn: Embedding function to generate query embeddings
        labels: List of label types to query (defaults to LINE_ITEM_LABELS)
        max_per_label: Maximum examples to fetch per label type

    Returns:
        LabelExamples containing validated examples by label type
    """
    if labels is None:
        labels = LINE_ITEM_LABELS

    result = LabelExamples(merchant_name=merchant_name)

    try:
        for label in labels:
            try:
                # Search using the label name as query text
                # This finds words that are semantically similar to the label concept
                query_embedding = embed_fn([label.lower().replace("_", " ")])

                query_result = chroma_client.query(
                    collection_name="words",
                    query_embeddings=query_embedding,
                    n_results=max_per_label * 3,  # Get extra to filter
                    where={
                        "$and": [
                            {"merchant_name": {"$eq": merchant_name}},
                            {
                                "label_status": {
                                    "$in": ["validated", "auto_suggested"]
                                }
                            },
                        ]
                    },
                    include=["metadatas", "distances"],
                )

                metadatas = extract_query_metadata_rows(query_result)
                count = 0
                for metadata in metadatas:
                    if count >= max_per_label:
                        break
                    valid_labels = parse_labels_from_metadata(
                        metadata,
                        array_field="valid_labels_array",
                    )
                    if label in valid_labels:
                        example = LabelExample(
                            word_text=metadata.get("text", ""),
                            label=label,
                            x_position=metadata.get("x", 0.5),
                            y_position=metadata.get("y", 0.5),
                            left_neighbor=metadata.get("left", "<EDGE>"),
                            right_neighbor=metadata.get("right", "<EDGE>"),
                        )
                        result.add_example(label, example)
                        count += 1

            except Exception as e:
                logger.debug("Error querying label %s: %s", label, e)
                continue

    except Exception as e:
        logger.warning("Error querying Chroma for label examples: %s", e)

    logger.info(
        "Found %d label examples for %s from Chroma",
        result.total_examples,
        merchant_name,
    )
    return result


def query_label_examples_simple(
    chroma_client: Any,
    merchant_name: str,
    embed_fn: Any | None = None,
    max_total: int = 50,
) -> LabelExamples:
    """Query ChromaDB for label examples using a simpler approach.

    Uses semantic search for "receipt line item" to find relevant words,
    then filters by merchant and groups by label.

    Args:
        chroma_client: ChromaDB client
        merchant_name: Name of the merchant to query
        embed_fn: Embedding function (required for query)
        max_total: Maximum total examples to fetch

    Returns:
        LabelExamples containing validated examples by label type
    """
    result = LabelExamples(merchant_name=merchant_name)

    if embed_fn is None:
        logger.warning("embed_fn required for query_label_examples_simple")
        return result

    try:
        # Generate embedding for a general query
        query_embedding = embed_fn(["product price total quantity"])

        # Query for validated words from this merchant
        query_result = chroma_client.query(
            collection_name="words",
            query_embeddings=query_embedding,
            n_results=max_total,
            where={
                "$and": [
                    {"merchant_name": {"$eq": merchant_name}},
                    {"label_status": {"$in": ["validated", "auto_suggested"]}},
                ]
            },
            include=["metadatas", "distances"],
        )

        metadatas = extract_query_metadata_rows(query_result)
        for metadata in metadatas:
            valid_labels = parse_labels_from_metadata(
                metadata,
                array_field="valid_labels_array",
            )

            for label in valid_labels:
                if label in LINE_ITEM_LABELS:
                    example = LabelExample(
                        word_text=metadata.get("text", ""),
                        label=label,
                        x_position=metadata.get("x", 0.5),
                        y_position=metadata.get("y", 0.5),
                        left_neighbor=metadata.get("left", "<EDGE>"),
                        right_neighbor=metadata.get("right", "<EDGE>"),
                    )
                    result.add_example(label, example)

    except Exception as e:
        logger.warning("Error querying Chroma for label examples: %s", e)

    logger.info(
        "Found %d label examples for %s from Chroma",
        result.total_examples,
        merchant_name,
    )
    return result


def _flatten_distances(query_result: dict[str, Any]) -> list[float | None]:
    """Return Chroma distances in the same order as metadata rows when present."""
    distances = query_result.get("distances") or []
    if distances and isinstance(distances[0], list):
        distances = distances[0]
    return [
        float(distance) if isinstance(distance, (int, float)) else None
        for distance in distances
    ]


def query_similar_merchant_examples_from_chroma(
    chroma_client: Any,
    merchant_name: str,
    embed_fn: Any,
    confusion_targets: list[ConfusionPatternTarget | dict[str, Any]],
    *,
    max_per_target: int = 4,
    n_results: int = 40,
) -> list[SimilarMerchantExample]:
    """Query validated examples from merchants similar to the target merchant.

    This mines cross-merchant evidence for confusion-driven synthesis. It
    intentionally excludes the current merchant so the returned examples are
    useful as hard negatives or contrast examples rather than duplicates of the
    same receipt template.
    """
    if not chroma_client or not embed_fn:
        return []

    targets = [
        (
            target
            if isinstance(target, ConfusionPatternTarget)
            else _target_from_dict(target)
        )
        for target in confusion_targets
    ]
    evidence: list[SimilarMerchantExample] = []
    seen: set[tuple[str, str, str]] = set()
    source_merchant = merchant_name.strip().lower()

    for target in targets:
        labels = _labels_for_confusion_target(target)
        if not labels:
            continue
        query = " ".join(
            [
                "receipt",
                target.pattern_family,
                *[label.lower().replace("_", " ") for label in labels],
            ]
        )
        try:
            query_embeddings = embed_fn([query])
            query_result = chroma_client.query(
                collection_name="words",
                query_embeddings=query_embeddings,
                n_results=n_results,
                where={
                    "label_status": {"$in": ["validated", "auto_suggested"]}
                },
                include=["metadatas", "distances"],
            )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            logger.debug("Similar-merchant Chroma query failed: %s", exc)
            continue

        metadatas = extract_query_metadata_rows(query_result)
        distances = _flatten_distances(query_result)
        kept_for_target = 0
        for idx, metadata in enumerate(metadatas):
            merchant = str(metadata.get("merchant_name") or "").strip()
            if not merchant or merchant.lower() == source_merchant:
                continue
            valid_labels = parse_labels_from_metadata(
                metadata,
                array_field="valid_labels_array",
            )
            label = next(
                (label for label in labels if label in valid_labels), None
            )
            if not label:
                continue
            text = str(metadata.get("text") or "").strip()
            if not text:
                continue
            key = (merchant.lower(), label, text.lower())
            if key in seen:
                continue
            seen.add(key)
            distance = distances[idx] if idx < len(distances) else None
            similarity = (
                max(0.0, 1.0 - (distance / 2.0))
                if distance is not None
                else None
            )
            evidence.append(
                SimilarMerchantExample(
                    merchant_name=merchant,
                    label=label,
                    word_text=text,
                    x_position=float(metadata.get("x", 0.5) or 0.5),
                    y_position=float(metadata.get("y", 0.5) or 0.5),
                    left_neighbor=str(metadata.get("left", "<EDGE>")),
                    right_neighbor=str(metadata.get("right", "<EDGE>")),
                    query=query,
                    similarity=similarity,
                )
            )
            kept_for_target += 1
            if kept_for_target >= max_per_target:
                break

    return evidence


def _word_layoutlm_bbox(word: Any) -> list[int]:
    """Convert a receipt word's normalized bounding box to LayoutLM scale."""
    bbox = getattr(word, "bounding_box", {}) or {}
    x0 = float(bbox.get("x", 0.5) or 0.5)
    y0 = float(bbox.get("y", 0.5) or 0.5)
    width = float(bbox.get("width", 0.04) or 0.04)
    height = float(bbox.get("height", 0.02) or 0.02)
    left = int(round(max(0.0, min(1.0, x0)) * 1000))
    top = int(round(max(0.0, min(1.0, y0)) * 1000))
    right = int(round(max(0.0, min(1.0, x0 + width)) * 1000))
    bottom = int(round(max(0.0, min(1.0, y0 + height)) * 1000))
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    return [left, top, right, bottom]


def build_receipt_structure(
    dynamo_client: DynamoClientProtocol,
    merchant_name: str,
    limit: int = 3,
    focus_on_line_items: bool = True,
    max_lines: int = 80,
) -> list[dict]:
    """Build structured receipt data for LLM analysis.

    Args:
        dynamo_client: DynamoDB client for fetching receipt data
        merchant_name: Name of the merchant to analyze
        limit: Maximum number of receipts to fetch
        focus_on_line_items: If True, prioritize the line items section over header
        max_lines: Maximum lines per receipt to include

    Returns:
        List of receipt structures with lines and word data
    """
    result = dynamo_client.get_receipt_places_by_merchant(
        merchant_name, limit=limit
    )
    receipt_places = result[0] if result else []

    if not receipt_places:
        logger.warning(
            "No receipt places found for merchant: %s", merchant_name
        )
        return []

    receipts_data = []

    for place in receipt_places:
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                place.image_id, place.receipt_id
            )
            # Handle implementations that return (items, last_key) tuple
            if isinstance(words, tuple):
                words = words[0]
            labels_result = dynamo_client.list_receipt_word_labels_for_receipt(
                place.image_id, place.receipt_id
            )
            labels = labels_result[0] if labels_result else []
        except Exception:
            logger.exception(
                "Error fetching receipt data for %s#%s",
                place.image_id,
                place.receipt_id,
            )
            continue

        if not words:
            continue

        # Build label lookup (only VALID labels)
        labels_by_word: dict[tuple[int, int], list[str]] = {}
        for label in labels:
            if label.validation_status == "VALID":
                key = (label.line_id, label.word_id)
                if key not in labels_by_word:
                    labels_by_word[key] = []
                labels_by_word[key].append(label.label)

        # Group words by line
        lines: dict[int, list] = {}
        for word in words:
            if word.line_id not in lines:
                lines[word.line_id] = []
            lines[word.line_id].append(word)

        # Sort lines by y-position (top to bottom)
        sorted_lines = []
        for line_id, line_words in lines.items():
            avg_y = sum(w.bounding_box["y"] for w in line_words) / len(
                line_words
            )
            line_words.sort(key=lambda w: w.bounding_box["x"])
            sorted_lines.append((line_id, avg_y, line_words))
        sorted_lines.sort(key=lambda x: -x[1])  # Highest y (top) first

        # Build structured representation - include ALL lines
        receipt_lines = []
        lines_with_line_item_labels = []

        for idx, (line_id, y_pos, line_words) in enumerate(sorted_lines):
            words_data = []
            has_line_item_label = False
            for word in line_words:
                key = (line_id, word.word_id)
                word_labels = labels_by_word.get(key, [])
                # Check if any label is a line item label
                if any(lbl in LINE_ITEM_LABELS for lbl in word_labels):
                    has_line_item_label = True
                words_data.append(
                    {
                        "text": word.text,
                        "x": round(word.bounding_box["x"], 3),
                        "bbox": _word_layoutlm_bbox(word),
                        "word_id": word.word_id,
                        "labels": word_labels if word_labels else None,
                    }
                )

            line_data = {
                "line_id": line_id,
                "y": round(y_pos, 3),
                "words": words_data,
            }
            receipt_lines.append(line_data)

            if has_line_item_label:
                lines_with_line_item_labels.append(idx)

        # Smart line selection: focus on line items section
        if focus_on_line_items and lines_with_line_item_labels:
            # Find the range of lines containing line item labels
            first_item_idx = min(lines_with_line_item_labels)
            last_item_idx = max(lines_with_line_item_labels)

            # Include some context before and after
            context_lines = 5
            start_idx = max(0, first_item_idx - context_lines)
            end_idx = min(
                len(receipt_lines), last_item_idx + context_lines + 1
            )

            # If the section is too large, prioritize from the start
            if end_idx - start_idx > max_lines:
                end_idx = start_idx + max_lines

            selected_lines = receipt_lines[start_idx:end_idx]
            logger.info(
                "Selected lines %d-%d (of %d total) for line items section",
                start_idx,
                end_idx,
                len(receipt_lines),
            )
        else:
            # No line item labels found - include all lines up to limit
            selected_lines = receipt_lines[:max_lines]

        if selected_lines:
            receipts_data.append(
                {
                    "receipt_id": f"{place.image_id[:8]}_{place.receipt_id}",
                    "image_id": place.image_id,
                    "receipt_num": place.receipt_id,
                    "line_count": len(selected_lines),
                    "total_lines": len(receipt_lines),
                    "lines": selected_lines,
                    "all_lines": receipt_lines,
                }
            )

    return receipts_data[:limit]


def build_discovery_prompt(
    merchant_name: str,
    receipts_data: list[dict],
    label_examples: LabelExamples | None = None,
    confusion_targets: (
        list[ConfusionPatternTarget | dict[str, Any]] | None
    ) = None,
    similar_merchant_examples: (
        list[SimilarMerchantExample | dict[str, Any]] | None
    ) = None,
) -> str:
    """Build the LLM prompt for pattern discovery.

    Args:
        merchant_name: Name of the merchant
        receipts_data: List of receipt structures from build_receipt_structure
        label_examples: Optional Chroma-validated label examples
        confusion_targets: Optional model-error targets from confusion matrix
        similar_merchant_examples: Optional Chroma evidence from other merchants

    Returns:
        Prompt string for the LLM
    """
    # Format receipt data for the prompt
    simplified = []
    for receipt in receipts_data[:3]:
        lines = []
        # Include more lines now that we're focused on line items section
        for line in receipt["lines"][:60]:
            words_str = " ".join(
                (
                    f"{w['text']}[{','.join(w['labels'])}]"
                    if w["labels"]
                    else w["text"]
                )
                for w in line["words"]
            )
            lines.append(f"  y={line['y']:.2f} | {words_str}")
        simplified.append("\n".join(lines))

    receipts_text = "\n\n---\n\n".join(simplified)

    # Build the label examples section if available
    label_examples_section = ""
    if label_examples and label_examples.total_examples > 0:
        label_examples_section = f"""

{label_examples.format_for_prompt()}

Use these validated examples to understand where each label type typically appears.
"""

    confusion_targets_section = ""
    if confusion_targets:
        confusion_targets_section = f"""

{format_confusion_targets_for_prompt(confusion_targets)}

Use these targets to identify merchant-specific failure patterns. Treat the heatmap
cells as layout constraints for synthetic receipts: preserve the merchant's normal
geometry, then add variants that isolate the confusing label pair.
"""

    similar_merchant_section = ""
    if similar_merchant_examples:
        formatted_examples = format_similar_merchant_examples_for_prompt(
            similar_merchant_examples
        )
        if formatted_examples:
            similar_merchant_section = f"""

{formatted_examples}

Use these cross-merchant examples as contrast evidence: identify what is stable
across similar merchants versus what is specific to "{merchant_name}". Fold this
evidence into each confusion pattern's pattern, heatmap_rationale, and
synthetic_receipt_strategy fields rather than adding extra JSON fields.
"""

    prompt = f"""Analyze the following receipt data from "{merchant_name}".

Each line shows: y-position | words (with [LABELS] for labeled words)
- Words WITHOUT labels are shown as plain text (e.g., product names, descriptions)
- Words WITH labels are shown as text[LABEL1,LABEL2] format
- The y-position indicates vertical position (higher = toward top of receipt)
{label_examples_section}
{confusion_targets_section}
{similar_merchant_section}
RECEIPT DATA:
{receipts_text}

---

STEP 1: Determine the receipt type by analyzing the structure:

- ITEMIZED: Multiple products/services listed with individual prices (grocery, retail, restaurant with itemized bill)
- SERVICE: Single charge, appointment-based, or non-itemized format (medical visit, spa service, parking, simple transaction)

STEP 2: If ITEMIZED, identify the line item patterns. If SERVICE, skip pattern analysis.

STEP 3: If confusion-driven heatmap targets are provided, explain the pattern
behind each confusion and propose synthetic receipt variants. When similar-
merchant evidence is provided, compare it to the target merchant before deciding
which pattern is merchant-specific and which pattern generalizes. Focus on
merchant name/header examples, price rows, address/contact blocks, and lookalike
background tokens that could increase the training set without diluting labels.

Respond with ONLY a JSON object (no other text):

If SERVICE receipt:
{{
  "merchant": "{merchant_name}",
  "receipt_type": "service",
  "receipt_type_reason": "brief explanation of why this is a service receipt",
  "item_structure": null,
  "lines_per_item": null,
  "item_start_marker": null,
  "item_end_marker": null,
  "barcode_pattern": null,
  "x_position_zones": null,
  "label_positions": null,
  "grouping_rule": null,
  "special_markers": [],
  "product_name_patterns": [],
  "confusion_patterns": [],
  "synthetic_receipt_guidance": []
}}

If ITEMIZED receipt:
{{
  "merchant": "{merchant_name}",
  "receipt_type": "itemized",
  "receipt_type_reason": "brief explanation of why this is an itemized receipt",
  "item_structure": "single-line" or "multi-line",
  "lines_per_item": {{"typical": N, "min": N, "max": N}},
  "item_start_marker": "description of what marks the start of a new item",
  "item_end_marker": "description of what marks the end of an item",
  "barcode_pattern": "regex pattern for SKU/barcode if found, or null",
  "x_position_zones": {{
    "left": [0.0, 0.3],
    "center": [0.3, 0.6],
    "right": [0.6, 1.0]
  }},
  "label_positions": {{
    "PRODUCT_NAME": "left" or "center" or "right" or "varies",
    "LINE_TOTAL": "left" or "center" or "right",
    "UNIT_PRICE": "left" or "center" or "right" or "not_found",
    "QUANTITY": "left" or "center" or "right" or "varies"
  }},
  "grouping_rule": "plain english description of how to group words into line items",
  "special_markers": ["list of special markers like <A>, *, etc. if found"],
  "product_name_patterns": ["common patterns for product names"],
  "confusion_patterns": [
    {{
      "actual_label": "label from confusion matrix, e.g. MERCHANT_NAME or O",
      "predicted_label": "label the model predicted, e.g. O or ADDRESS_LINE",
      "pattern": "specific merchant/layout pattern that explains the confusion",
      "heatmap_rationale": "why the observed x/y zones matter",
      "synthetic_receipt_strategy": "how to synthesize receipts for this target"
    }}
  ],
  "synthetic_receipt_guidance": [
    "short actionable augmentation rule, e.g. add hard-negative header lookalikes near merchant-name zones"
  ]
}}

Respond with ONLY the JSON object, no markdown code blocks or other text."""

    return prompt


def discover_patterns_with_llm(
    prompt: str,
    config: PatternDiscoveryConfig | None = None,
    trace_ctx: Any | None = None,
) -> dict | None:
    """Call LLM to discover patterns.

    Args:
        prompt: The prompt to send to the LLM
        config: Configuration for the LLM call (uses env vars if None)
        trace_ctx: Optional LangSmith trace context for tracing

    Returns:
        Parsed JSON response from LLM, or None if failed
    """
    if config is None:
        config = PatternDiscoveryConfig.from_env()

    if config.disable_paid_llm:
        logger.warning(
            "Paid LLM pattern discovery disabled; using deterministic fallback"
        )
        return None

    if not config.openrouter_api_key:
        logger.error("OPENROUTER_API_KEY not set")
        return None

    llm_inputs = {
        "model": config.openrouter_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a receipt analysis expert. Respond only with valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    strict_structured_output = _is_strict_structured_output_enabled()

    try:
        # If we have a trace context with child_trace, use it
        if trace_ctx is not None:
            return _call_llm_with_tracing(
                prompt,
                config,
                llm_inputs,
                trace_ctx,
                strict_structured_output=strict_structured_output,
            )

        # No tracing - direct call
        return _call_llm_direct(
            config,
            llm_inputs,
            strict_structured_output=strict_structured_output,
        )

    except json.JSONDecodeError as e:
        logger.exception("Failed to parse LLM response as JSON: %s", e)
        return None
    except Exception:
        logger.exception("LLM call failed")
        return None


def _call_llm_direct(
    config: PatternDiscoveryConfig,
    llm_inputs: dict,
    *,
    strict_structured_output: bool,
) -> dict | None:
    """Make LLM call without tracing."""
    request_payload: dict[str, Any] = {
        "model": config.openrouter_model,
        "messages": llm_inputs["messages"],
        "temperature": 0.0,
    }
    if strict_structured_output:
        request_payload["response_format"] = _build_pattern_response_format()

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{config.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
        )
        response.raise_for_status()
        result = response.json()

    # OpenRouter uses OpenAI-compatible response format
    content = (
        result.get("choices", [{}])[0].get("message", {}).get("content", "")
    )
    return _parse_llm_response(
        content,
        strict_structured_output=strict_structured_output,
    )


def _call_llm_with_tracing(
    prompt: str,
    config: PatternDiscoveryConfig,
    llm_inputs: dict,
    trace_ctx: Any,
    *,
    strict_structured_output: bool,
) -> dict | None:
    """Make LLM call with LangSmith tracing."""
    # Import child_trace dynamically to avoid import errors when not in Lambda
    try:
        from tracing import child_trace  # type: ignore[import-not-found]
    except ImportError:
        # Fall back to direct call if tracing not available
        logger.warning("Tracing not available, falling back to direct call")
        return _call_llm_direct(
            config,
            llm_inputs,
            strict_structured_output=strict_structured_output,
        )

    with child_trace(
        "llm_pattern_discovery",
        trace_ctx,
        run_type="llm",
        metadata={
            "model": config.openrouter_model,
            "prompt_length": len(prompt),
        },
        inputs=llm_inputs,
    ) as llm_trace_ctx:
        request_payload: dict[str, Any] = {
            "model": config.openrouter_model,
            "messages": llm_inputs["messages"],
            "temperature": 0.0,
        }
        if strict_structured_output:
            request_payload["response_format"] = (
                _build_pattern_response_format()
            )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{config.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            response.raise_for_status()
            result = response.json()

        # OpenRouter uses OpenAI-compatible response format
        content = (
            result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        # Capture raw output in trace
        llm_trace_ctx.set_outputs(
            {
                "raw_response": (
                    content[:2000] if len(content) > 2000 else content
                ),
                "model": result.get("model"),
                "finish_reason": result.get("choices", [{}])[0].get(
                    "finish_reason"
                ),
            }
        )

        return _parse_llm_response(
            content,
            strict_structured_output=strict_structured_output,
        )


def _parse_llm_response(
    content: str,
    *,
    strict_structured_output: bool = True,
) -> dict | None:
    """Parse LLM response, handling markdown code blocks.

    First attempts to parse using the PatternDiscoveryResponse Pydantic model,
    which validates the schema and constrains enum values.
    Falls back to manual JSON parsing if structured parsing fails.
    """
    content = extract_json_from_response(content)

    # Try to parse JSON first
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return None

    if not isinstance(parsed, dict):
        logger.warning("LLM response JSON was not an object")
        return None

    # Try structured validation (validates schema and enum values)
    try:
        structured_response = PatternDiscoveryResponse.model_validate(parsed)
        return structured_response.to_dict()
    except ValidationError as e:
        if strict_structured_output:
            logger.warning(
                "Strict structured validation failed for pattern discovery: %s",
                e,
            )
            return None

        logger.debug(
            "Structured validation failed, returning raw parsed JSON: %s",
            e,
        )
        return parsed


def get_default_patterns(merchant_name: str, reason: str = "unknown") -> dict:
    """Return default patterns when discovery fails.

    Args:
        merchant_name: Name of the merchant
        reason: Why default patterns are being used

    Returns:
        Default pattern dictionary matching the flat schema
    """
    return {
        # Metadata
        "merchant": merchant_name,
        "receipt_type": "unknown",
        "receipt_type_reason": reason,
        "auto_generated": True,
        # Structure
        "item_structure": "unknown",
        "lines_per_item": {"typical": 2, "min": 1, "max": 5},
        "item_start_marker": "PRODUCT_NAME or barcode",
        "item_end_marker": "LINE_TOTAL label",
        "grouping_rule": "Group all words between consecutive LINE_TOTAL labels",
        # Position info
        "label_positions": {
            "PRODUCT_NAME": "left",
            "LINE_TOTAL": "right",
            "UNIT_PRICE": "right",
            "QUANTITY": "varies",
        },
        "x_position_zones": {
            "left": [0.0, 0.3],
            "center": [0.3, 0.6],
            "right": [0.6, 1.0],
        },
        # Pattern matching
        "barcode_pattern": r"\d{10,14}",
        "special_markers": [],
        "product_name_patterns": [],
    }
