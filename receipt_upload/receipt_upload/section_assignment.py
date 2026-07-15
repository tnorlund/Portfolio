"""Deterministic row-to-section assignment from QA-derived order priors."""

# The model builder and segment decoder intentionally keep their measured
# factors together so training and inference remain directly auditable.
# pylint: disable=too-many-instance-attributes,too-many-locals,too-many-branches

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from itertools import groupby
from statistics import fmean, pstdev
from typing import Any, Protocol

from receipt_dynamo.amounts import looks_like_receipt_amount
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities import ReceiptRow, ReceiptSection

MODEL_SOURCE = "upload-determinism-v1"
_TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_QUANTITY_RE = re.compile(
    r"(?:\b\d+\s*(?:@|x)\s*\$?\d)|(?:\b(?:each|ea)\b)|(?:/\s*lb\b)",
    re.IGNORECASE,
)
_EPSILON = 1e-9
_AMOUNT_CONTEXT_RADIUS = 2


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
    x_span: float
    alpha_ratio: float
    has_amount: float
    amount_density: float
    has_quantity: float
    tokens: tuple[str, ...]
    token_evidence: tuple[tuple[str, float], ...] = ()

    def numeric(self) -> dict[str, float]:
        """Return the scalar feature vector used by the learned model."""

        return {
            "position": self.position,
            "x_span": self.x_span,
            "alpha_ratio": self.alpha_ratio,
            "amount_density": self.amount_density,
        }

    def binary(self) -> dict[str, float]:
        """Return Bernoulli evidence kept separate from Gaussian features."""

        return {
            "has_amount": self.has_amount,
            "has_quantity": self.has_quantity,
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


def _tokens(text: str) -> tuple[str, ...]:
    """Canonicalize unstable numbers while preserving lexical evidence."""

    result = []
    for token in _TOKEN_RE.findall(text.casefold()):
        if looks_like_receipt_amount(token):
            result.append("__amount__")
        elif token.isdigit():
            result.append("__number__")
        else:
            result.append(token)
    return tuple(result)


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
    texts = [_row_text(row, lines_by_id) for row in ordered]
    tokens_by_row = [_tokens(text) for text in texts]
    amount_flags = [
        float(row.amount_text is not None or "__amount__" in tokens)
        for row, tokens in zip(ordered, tokens_by_row, strict=True)
    ]
    features: list[RowFeatures] = []
    for index, (row, text, tokens) in enumerate(
        zip(ordered, texts, tokens_by_row, strict=True)
    ):
        letters = sum(char.isalpha() for char in text)
        digits = sum(char.isdigit() for char in text)
        visible = max(letters + digits, 1)
        context_start = max(0, index - _AMOUNT_CONTEXT_RADIUS)
        context_end = min(count, index + _AMOUNT_CONTEXT_RADIUS + 1)
        features.append(
            RowFeatures(
                row=row,
                position=(index / (count - 1) if count > 1 else 0.5),
                x_span=row.x_max - row.x_min,
                alpha_ratio=letters / visible,
                has_amount=amount_flags[index],
                amount_density=fmean(amount_flags[context_start:context_end]),
                has_quantity=float(bool(_QUANTITY_RE.search(text))),
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
    binary_by_section: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    durations: dict[str, list[float]] = defaultdict(list)
    pooled_durations: list[float] = []
    token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    transitions: dict[str, Counter[str]] = defaultdict(Counter)
    section_counts: Counter[str] = Counter()
    pooled: dict[str, list[float]] = defaultdict(list)
    for receipt in labeled_receipts:
        for features, section in receipt:
            section_counts[section] += 1
            for name, value in features.numeric().items():
                by_section[section][name].append(value)
                pooled[name].append(value)
            for name, value in features.binary().items():
                binary_by_section[section][name].append(value)
            token_counts[section].update(features.tokens)
        runs = [
            (section, len(list(group)))
            for section, group in groupby((section for _, section in receipt))
        ]
        if not runs:
            continue
        transitions["<START>"][runs[0][0]] += 1
        for index, (section, duration) in enumerate(runs):
            log_duration = math.log(duration)
            durations[section].append(log_duration)
            pooled_durations.append(log_duration)
            destination = (
                runs[index + 1][0] if index + 1 < len(runs) else "<END>"
            )
            transitions[section][destination] += 1

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
    vocabulary = {
        token for counts in token_counts.values() for token in counts
    }
    all_token_counts = sum(token_counts.values(), Counter())
    all_token_total = sum(all_token_counts.values())
    pooled_duration_variance = _distribution(pooled_durations)["std"] ** 2
    for section in sections:
        section_model = {
            "support": section_counts[section],
            "features": {
                name: _regularized_distribution(values, pooled_variance[name])
                for name, values in sorted(by_section[section].items())
            },
            "binary_features": {
                name: {"probability": (sum(values) + 1) / (len(values) + 2)}
                for name, values in sorted(binary_by_section[section].items())
            },
            "duration": _regularized_distribution(
                durations[section], pooled_duration_variance
            ),
        }
        if include_tokens:
            section_total = sum(token_counts[section].values())
            other_total = all_token_total - section_total
            vocabulary_size = len(vocabulary) + 1
            section_model["token_log_odds"] = {
                token: math.log(
                    (count + 1) / (section_total + vocabulary_size)
                )
                - math.log(
                    (all_token_counts[token] - count + 1)
                    / (other_total + vocabulary_size)
                )
                for token, count in token_counts[section].most_common(200)
            }
        feature_model[section] = section_model

    transition_model: dict[str, dict[str, float]] = {}
    for source in ["<START>"] + sections:
        destinations = (
            sections
            if source == "<START>"
            else [section for section in sections if section != source]
            + ["<END>"]
        )
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
        "assets", "section_order_priors_v2.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _gaussian_score(value: float, distribution: Mapping[str, float]) -> float:
    std = max(float(distribution["std"]), _EPSILON)
    z_score = (value - float(distribution["mean"])) / std
    return -0.5 * z_score * z_score - math.log(std)


def _bernoulli_score(value: float, distribution: Mapping[str, float]) -> float:
    probability = min(
        max(float(distribution["probability"]), _EPSILON), 1 - _EPSILON
    )
    return math.log(probability if value else 1 - probability)


def _emission(
    features: RowFeatures,
    section: str,
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
    binary_distributions = section_model["binary_features"]
    fallback_binary = fallback_model["binary_features"]
    scores.extend(
        _bernoulli_score(
            value,
            binary_distributions.get(name, fallback_binary[name]),
        )
        for name, value in features.binary().items()
    )
    if features.token_evidence:
        scores.append(float(dict(features.token_evidence).get(section, 0.0)))
    else:
        token_log_odds = section_model.get(
            "token_log_odds", fallback_model.get("token_log_odds", {})
        )
        scores.extend(
            float(token_log_odds[token])
            for token in features.tokens
            if token in token_log_odds
        )
    return sum(scores)


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


def _duration_score(
    prior: Mapping[str, Any],
    global_prior: Mapping[str, Any],
    section: str,
    duration: int,
) -> float:
    section_model = prior["section_models"].get(
        section, global_prior["section_models"][section]
    )
    distribution = section_model.get(
        "duration", global_prior["section_models"][section]["duration"]
    )
    return _gaussian_score(math.log(duration), distribution)


def assign_row_sections(
    rows: Sequence[ReceiptRow],
    lines: Sequence[Any],
    model: Mapping[str, Any],
    merchant_name: str | None = None,
) -> list[RowAssignment]:
    """Extract observable row evidence and decode deterministic sections."""

    return assign_feature_sections(
        extract_row_features(rows, lines), model, merchant_name
    )


def assign_feature_sections(
    features: Sequence[RowFeatures],
    model: Mapping[str, Any],
    merchant_name: str | None = None,
) -> list[RowAssignment]:
    """Assign sections with a learned semi-Markov segment decoder."""

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
                section,
                prior["section_models"].get(
                    section, global_prior["section_models"][section]
                ),
                global_prior["section_models"][section],
            )
            for section in sections
        ]
        for row in features
    ]
    prefixes = [[0.0] for _ in sections]
    for state in range(len(sections)):
        for row_scores in emissions:
            prefixes[state].append(prefixes[state][-1] + row_scores[state])

    # Each cell stores the best score plus (segment start, previous state).
    paths: list[list[tuple[float, tuple[int, int | None]]]] = []
    for end in range(len(features)):
        current: list[tuple[float, tuple[int, int | None]]] = []
        for state, section in enumerate(sections):
            candidates: list[tuple[float, tuple[int, int | None]]] = []
            for start in range(end + 1):
                duration = end - start + 1
                segment_score = (
                    prefixes[state][end + 1] - prefixes[state][start]
                ) + _duration_score(prior, global_prior, section, duration)
                if start == 0:
                    transition = _transition(
                        prior, global_prior, "<START>", section
                    )
                    candidates.append(
                        (
                            math.log(max(transition, _EPSILON))
                            + segment_score,
                            (start, None),
                        )
                    )
                    continue
                for previous_state, source in enumerate(sections):
                    if previous_state == state:
                        continue
                    transition = _transition(
                        prior, global_prior, source, section
                    )
                    candidates.append(
                        (
                            paths[start - 1][previous_state][0]
                            + math.log(max(transition, _EPSILON))
                            + segment_score,
                            (start, previous_state),
                        )
                    )
            current.append(
                max(
                    candidates,
                    key=lambda candidate: (
                        candidate[0],
                        -candidate[1][0],
                        -(candidate[1][1] or 0),
                    ),
                )
            )
        paths.append(current)

    state = max(
        range(len(sections)),
        key=lambda index: paths[-1][index][0]
        + math.log(
            max(
                _transition(prior, global_prior, sections[index], "<END>"),
                _EPSILON,
            )
        ),
    )
    states = [0] * len(features)
    end = len(features) - 1
    while end >= 0:
        start, prior_state = paths[end][state][1]
        for row_index in range(start, end + 1):
            states[row_index] = state
        if prior_state is None:
            break
        end = start - 1
        state = prior_state
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
