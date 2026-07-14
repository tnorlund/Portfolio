"""Deterministic row-to-section assignment from QA-derived order priors."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from statistics import fmean, pstdev
from typing import Any, Protocol

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities import ReceiptRow, ReceiptSection

MODEL_SOURCE = "upload-determinism-v1"
_TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_EPSILON = 1e-9


class SectionWriter(Protocol):
    """Dynamo operations required by the assignment adapter."""

    def add_receipt_section(self, section: ReceiptSection) -> None:
        """Conditionally add one section."""

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptSection]:
        """Read the receipt current section rows."""

    def update_receipt_section(self, section: ReceiptSection) -> None:
        """Conditionally update an existing section."""


@dataclass(frozen=True)
class RowFeatures:
    """Receipt-normalized features for one visual row."""

    row: ReceiptRow
    position: float
    y_center: float
    x_span: float
    alpha_ratio: float
    digit_ratio: float
    has_amount: float
    tokens: tuple[str, ...]

    def numeric(self) -> dict[str, float]:
        """Return the scalar feature vector used by the learned model."""

        return {
            "position": self.position,
            "y_center": self.y_center,
            "x_span": self.x_span,
            "alpha_ratio": self.alpha_ratio,
            "digit_ratio": self.digit_ratio,
            "has_amount": self.has_amount,
        }


@dataclass(frozen=True)
class RowAssignment:
    """One deterministic row prediction."""

    row: ReceiptRow
    section_type: str
    confidence: float


def normalize_merchant_key(name: str | None) -> str:
    """Normalize a merchant name without applying business-specific aliases."""

    if not name:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", name.casefold()))


def _row_text(row: ReceiptRow, lines_by_id: Mapping[int, Any]) -> str:
    return " ".join(
        str(lines_by_id[line_id].text)
        for line_id in row.line_ids
        if line_id in lines_by_id
    )


def extract_row_features(
    rows: Sequence[ReceiptRow],
    lines: Sequence[Any],
) -> list[RowFeatures]:
    """Extract row and zone features in stable top-to-bottom order."""

    ordered = sorted(
        rows, key=lambda row: (-(row.y_min + row.y_max), row.row_id)
    )
    lines_by_id = {line.line_id: line for line in lines}
    count = len(ordered)
    features: list[RowFeatures] = []
    for index, row in enumerate(ordered):
        text = _row_text(row, lines_by_id)
        letters = sum(char.isalpha() for char in text)
        digits = sum(char.isdigit() for char in text)
        visible = max(letters + digits, 1)
        tokens = tuple(_TOKEN_RE.findall(text.casefold()))
        features.append(
            RowFeatures(
                row=row,
                position=(index / (count - 1) if count > 1 else 0.5),
                y_center=(row.y_min + row.y_max) / 2.0,
                x_span=row.x_max - row.x_min,
                alpha_ratio=letters / visible,
                digit_ratio=digits / visible,
                has_amount=float(row.amount_text is not None),
                tokens=tokens,
            )
        )
    return features


def _distribution(values: Sequence[float]) -> dict[str, float]:
    mean = fmean(values)
    return {
        "mean": mean,
        "std": pstdev(values) if len(values) > 1 else 0.0,
    }


def learn_prior(
    labeled_receipts: Sequence[Sequence[tuple[RowFeatures, str]]],
    *,
    include_tokens: bool = True,
) -> dict[str, Any]:
    """Fit one order/feature prior from VALID receipt assignments only."""

    by_section: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    transitions: dict[str, Counter[str]] = defaultdict(Counter)
    section_counts: Counter[str] = Counter()
    pooled: dict[str, list[float]] = defaultdict(list)
    for receipt in labeled_receipts:
        previous = "<START>"
        for features, section in receipt:
            section_counts[section] += 1
            for name, value in features.numeric().items():
                by_section[section][name].append(value)
                pooled[name].append(value)
            token_counts[section].update(features.tokens)
            transitions[previous][section] += 1
            previous = section
        transitions[previous]["<END>"] += 1

    sections = sorted(
        section_counts,
        key=lambda section: (
            _distribution(by_section[section]["position"])["mean"],
            section,
        ),
    )
    feature_model: dict[str, Any] = {}
    pooled_variance = {
        name: _distribution(values)["std"] ** 2
        for name, values in pooled.items()
    }
    vocabulary = sorted(
        {token for counts in token_counts.values() for token in counts}
    )
    for section in sections:
        total = sum(token_counts[section].values()) + len(vocabulary) + 1
        section_model = {
            "support": section_counts[section],
            "features": {
                name: _regularized_distribution(values, pooled_variance[name])
                for name, values in sorted(by_section[section].items())
            },
        }
        if include_tokens:
            section_model["tokens"] = {
                token: (count + 1) / total
                for token, count in token_counts[section].most_common(100)
            }
            section_model["unknown_token_probability"] = 1 / total
        feature_model[section] = section_model

    transition_model: dict[str, dict[str, float]] = {}
    destinations = sections + ["<END>"]
    for source in ["<START>"] + sections:
        counts = transitions[source]
        total = sum(counts.values()) + len(destinations)
        transition_model[source] = {
            destination: (counts[destination] + 1) / total
            for destination in destinations
        }
    return {
        "receipt_count": len(labeled_receipts),
        "sections": sections,
        "section_models": feature_model,
        "transitions": transition_model,
    }


def _regularized_distribution(
    values: Sequence[float], pooled_variance: float
) -> dict[str, float]:
    """Estimate spread with a corpus-derived one-observation prior."""

    observed = _distribution(values)
    count = len(values)
    variance = (observed["std"] ** 2 * count + pooled_variance) / (count + 1)
    return {"mean": observed["mean"], "std": math.sqrt(variance)}


def load_prior_model() -> dict[str, Any]:
    """Load the committed, QA-derived section-order model."""

    path = files("receipt_upload").joinpath(
        "assets", "section_order_priors_v1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _gaussian_score(value: float, distribution: Mapping[str, float]) -> float:
    std = max(float(distribution["std"]), _EPSILON)
    z_score = (value - float(distribution["mean"])) / std
    return -0.5 * z_score * z_score - math.log(std)


def _emission(
    features: RowFeatures,
    section_model: Mapping[str, Any],
    fallback_model: Mapping[str, Any],
) -> float:
    distributions = section_model["features"]
    fallback_distributions = fallback_model["features"]
    scores = [
        _gaussian_score(
            value,
            (
                distributions[name]
                if float(distributions[name]["std"]) > _EPSILON
                else fallback_distributions[name]
            ),
        )
        for name, value in features.numeric().items()
    ]
    tokens = section_model.get("tokens", {})
    unknown = float(section_model.get("unknown_token_probability", _EPSILON))
    if features.tokens and "tokens" in section_model:
        scores.append(
            fmean(
                math.log(float(tokens.get(token, unknown)))
                for token in features.tokens
            )
        )
    return fmean(scores)


def _confidence(scores: Sequence[float], chosen: int) -> float:
    peak = max(scores)
    weights = [math.exp(score - peak) for score in scores]
    return weights[chosen] / max(sum(weights), _EPSILON)


def _merchant_prior(
    model: Mapping[str, Any], merchant_name: str | None
) -> Mapping[str, Any]:
    key = normalize_merchant_key(merchant_name)
    return model.get("merchants", {}).get(key) or model["global"]


def _ordered_sections(
    prior: Mapping[str, Any], global_prior: Mapping[str, Any]
) -> list[str]:
    """Order the full vocabulary using merchant means where available."""

    merchant_models = prior["section_models"]
    return sorted(
        global_prior["sections"],
        key=lambda section: (
            (
                merchant_models.get(section)
                or global_prior["section_models"][section]
            )["features"]["position"]["mean"],
            section,
        ),
    )


def _transition(
    prior: Mapping[str, Any],
    global_prior: Mapping[str, Any],
    source: str,
    destination: str,
) -> float:
    merchant_value = (
        prior.get("transitions", {}).get(source, {}).get(destination)
    )
    if merchant_value is not None:
        return float(merchant_value)
    return float(
        global_prior["transitions"].get(source, {}).get(destination, _EPSILON)
    )


def assign_row_sections(
    rows: Sequence[ReceiptRow],
    lines: Sequence[Any],
    model: Mapping[str, Any],
    merchant_name: str | None = None,
) -> list[RowAssignment]:
    """Assign sections with learned emissions and monotone Viterbi order."""

    features = extract_row_features(rows, lines)
    if not features:
        return []
    prior = _merchant_prior(model, merchant_name)
    global_prior = model["global"]
    sections = _ordered_sections(prior, global_prior)
    if not sections:
        return []
    emissions = [
        [
            _emission(
                row,
                prior["section_models"].get(
                    section, global_prior["section_models"][section]
                ),
                global_prior["section_models"][section],
            )
            for section in sections
        ]
        for row in features
    ]
    paths: list[list[tuple[float, int | None]]] = []
    first: list[tuple[float, int | None]] = []
    for state, section in enumerate(sections):
        transition = _transition(prior, global_prior, "<START>", section)
        first.append(
            (math.log(max(transition, _EPSILON)) + emissions[0][state], None)
        )
    paths.append(first)
    for row_index in range(1, len(features)):
        current: list[tuple[float, int | None]] = []
        for state, section in enumerate(sections):
            candidates = []
            for previous in range(state + 1):
                source = sections[previous]
                transition = _transition(prior, global_prior, source, section)
                candidates.append(
                    (
                        paths[row_index - 1][previous][0]
                        + math.log(max(transition, _EPSILON))
                        + emissions[row_index][state],
                        previous,
                    )
                )
            current.append(
                max(
                    candidates,
                    key=lambda candidate: (candidate[0], -candidate[1]),
                )
            )
        paths.append(current)

    state = max(range(len(sections)), key=lambda index: paths[-1][index][0])
    states = [state]
    for row_index in range(len(features) - 1, 0, -1):
        prior_state = paths[row_index][states[-1]][1]
        if (
            prior_state is None
        ):  # pragma: no cover - impossible after first row
            prior_state = 0
        states.append(prior_state)
    states.reverse()
    return [
        RowAssignment(
            row=row.row,
            section_type=sections[state_index],
            confidence=_confidence(emissions[index], state_index),
        )
        for index, (row, state_index) in enumerate(
            zip(features, states, strict=True)
        )
    ]


def sections_from_assignments(
    assignments: Sequence[RowAssignment],
) -> list[ReceiptSection]:
    """Aggregate row predictions into additive ReceiptSection entities."""

    if not assignments:
        return []
    grouped: dict[str, list[RowAssignment]] = defaultdict(list)
    for assignment in assignments:
        grouped[assignment.section_type].append(assignment)
    created_at = datetime.now(timezone.utc)
    sections = []
    for section_type, group in sorted(grouped.items()):
        first = group[0].row
        sections.append(
            ReceiptSection(
                image_id=first.image_id,
                receipt_id=first.receipt_id,
                section_type=section_type,
                line_ids=[
                    line_id for item in group for line_id in item.row.line_ids
                ],
                row_ids=[item.row.row_id for item in group],
                confidence=fmean(item.confidence for item in group),
                validation_status=ValidationStatus.PENDING.value,
                model_source=MODEL_SOURCE,
                created_at=created_at,
            )
        )
    return sections


def assign_and_persist_sections(
    dynamo: SectionWriter,
    rows: Sequence[ReceiptRow],
    lines: Sequence[Any],
    merchant_name: str | None,
    model: Mapping[str, Any] | None = None,
) -> tuple[list[ReceiptSection], dict[int, str]]:
    """Persist only absent sections and return current line assignments."""

    if not rows:
        return [], {}
    predictions = sections_from_assignments(
        assign_row_sections(
            rows, lines, model or load_prior_model(), merchant_name
        )
    )
    existing = dynamo.get_receipt_sections_from_receipt(
        rows[0].image_id, rows[0].receipt_id
    )
    existing_types = {section.section_type for section in existing}
    created: list[ReceiptSection] = []
    for section in predictions:
        if section.section_type in existing_types:
            continue
        try:
            dynamo.add_receipt_section(section)
            created.append(section)
            existing_types.add(section.section_type)
        except EntityAlreadyExistsError:
            pass
    current = dynamo.get_receipt_sections_from_receipt(
        rows[0].image_id, rows[0].receipt_id
    )
    by_line = {
        line_id: str(section.section_type)
        for section in current
        if section.validation_status != ValidationStatus.INVALID.value
        for line_id in section.line_ids
    }
    return created, by_line
