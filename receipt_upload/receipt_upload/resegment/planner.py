"""Pure planning utilities for splitting one receipt into new segments."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

WordRef = tuple[int, int]
LetterRef = tuple[int, int, int]


class ResegmentPlanError(ValueError):
    """Raised when a re-segmentation request is ambiguous or unsafe."""


def _value(entity: Any, name: str, default: Any = None) -> Any:
    if isinstance(entity, Mapping):
        return entity.get(name, default)
    return getattr(entity, name, default)


def _word_ref(entity: Any) -> WordRef:
    return int(_value(entity, "line_id")), int(_value(entity, "word_id"))


def _line_id(entity: Any) -> int:
    return int(_value(entity, "line_id"))


def _letter_ref(entity: Any) -> LetterRef:
    line_id, word_id = _word_ref(entity)
    return line_id, word_id, int(_value(entity, "letter_id"))


def _ref_dict(ref: WordRef) -> dict[str, int]:
    return {"line_id": ref[0], "word_id": ref[1]}


def _parse_word_refs(raw_refs: Iterable[Mapping[str, Any]]) -> set[WordRef]:
    refs: set[WordRef] = set()
    for raw_ref in raw_refs:
        line_id = int(raw_ref["line_id"])
        word_ids = raw_ref.get("word_ids")
        has_word_id = "word_id" in raw_ref
        if word_ids is not None and has_word_id:
            raise ResegmentPlanError(
                "A word reference must use word_id or word_ids, not both"
            )
        if word_ids is None and not has_word_id:
            raise ResegmentPlanError(
                "A word reference requires either word_id or word_ids"
            )
        if word_ids is None:
            word_ids = [raw_ref["word_id"]]
        refs.update((line_id, int(word_id)) for word_id in word_ids)
    return refs


def _expand_selection(
    *,
    line_ids: Iterable[int],
    raw_word_refs: Iterable[Mapping[str, Any]],
    refs_by_line: Mapping[int, set[WordRef]],
    known_refs: set[WordRef],
    destination: str,
) -> set[WordRef]:
    selected: set[WordRef] = set()
    requested_lines = {int(line_id) for line_id in line_ids}
    unknown_lines = sorted(requested_lines - set(refs_by_line))
    if unknown_lines:
        raise ResegmentPlanError(
            f"{destination} references unknown line_ids: {unknown_lines}"
        )
    for line_id in requested_lines:
        selected.update(refs_by_line[line_id])

    explicit_refs = _parse_word_refs(raw_word_refs)
    unknown_refs = sorted(explicit_refs - known_refs)
    if unknown_refs:
        rendered = [_ref_dict(ref) for ref in unknown_refs]
        raise ResegmentPlanError(
            f"{destination} references unknown words: {rendered}"
        )
    selected.update(explicit_refs)
    return selected


def _labels_by_ref(labels: Iterable[Any]) -> dict[WordRef, list[Any]]:
    grouped: dict[WordRef, list[Any]] = defaultdict(list)
    for label in labels:
        grouped[_word_ref(label)].append(label)
    return grouped


def normalize_resegmentation_plan(
    *,
    words: Iterable[Any],
    labels: Iterable[Any],
    segments: Iterable[Mapping[str, Any]],
    discard_line_ids: Iterable[int] = (),
    discard_word_refs: Iterable[Mapping[str, Any]] = (),
    discard_reason: str | None = None,
    allow_labeled_discard: bool = False,
) -> dict[str, Any]:
    """Expand line shorthands into a complete, validated word assignment.

    Every source word must land in exactly one output segment or in the
    explicit discard bucket. The returned word references are canonical and
    suitable for hashing and later apply-time verification.
    """
    source_words = list(words)
    source_labels = list(labels)
    if not source_words:
        raise ResegmentPlanError("The source receipt has no words")

    known_refs = {_word_ref(word) for word in source_words}
    if len(known_refs) != len(source_words):
        raise ResegmentPlanError(
            "The source receipt contains duplicate word keys"
        )

    refs_by_line: dict[int, set[WordRef]] = defaultdict(set)
    for ref in known_refs:
        refs_by_line[ref[0]].add(ref)

    labels_by_ref = _labels_by_ref(source_labels)
    orphan_label_refs = sorted(set(labels_by_ref) - known_refs)
    if orphan_label_refs:
        raise ResegmentPlanError(
            "The source receipt contains labels without matching words: "
            f"{[_ref_dict(ref) for ref in orphan_label_refs]}"
        )
    normalized_segments: list[dict[str, Any]] = []
    owners: dict[WordRef, str] = {}
    segment_keys: set[str] = set()

    for raw_segment in segments:
        segment_key = str(raw_segment.get("segment_key", "")).strip()
        if not segment_key:
            raise ResegmentPlanError("Every segment requires a segment_key")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", segment_key):
            raise ResegmentPlanError(
                "segment_key must be 1-64 URL-safe characters and start "
                "with a letter or number"
            )
        if segment_key in segment_keys:
            raise ResegmentPlanError(f"Duplicate segment_key: {segment_key}")
        segment_keys.add(segment_key)

        selected = _expand_selection(
            line_ids=raw_segment.get("include_line_ids", ()),
            raw_word_refs=raw_segment.get("include_word_refs", ()),
            refs_by_line=refs_by_line,
            known_refs=known_refs,
            destination=f"segment {segment_key}",
        )
        if not selected:
            raise ResegmentPlanError(
                f"Segment {segment_key} does not select any words"
            )

        duplicates = sorted(ref for ref in selected if ref in owners)
        if duplicates:
            rendered = [
                {**_ref_dict(ref), "already_assigned_to": owners[ref]}
                for ref in duplicates
            ]
            raise ResegmentPlanError(
                f"Segment {segment_key} duplicates assignments: {rendered}"
            )
        for ref in selected:
            owners[ref] = segment_key

        status_counts = Counter(
            str(_value(label, "validation_status", "NONE") or "NONE")
            for ref in selected
            for label in labels_by_ref.get(ref, ())
        )
        place_policy = (
            str(raw_segment.get("place_policy", "none")).strip().lower()
        )
        if place_policy not in {"inherit", "none"}:
            raise ResegmentPlanError(
                f"Unsupported place_policy for segment {segment_key}: {place_policy}"
            )
        normalized_segments.append(
            {
                "segment_key": segment_key,
                "word_refs": [_ref_dict(ref) for ref in sorted(selected)],
                "word_count": len(selected),
                "label_count": sum(status_counts.values()),
                "label_status_counts": dict(sorted(status_counts.items())),
                "place_policy": place_policy,
            }
        )

    if not normalized_segments:
        raise ResegmentPlanError("At least one output segment is required")
    if len(normalized_segments) > 23:
        raise ResegmentPlanError(
            "A re-segmentation can create at most 23 segments"
        )

    discarded = _expand_selection(
        line_ids=discard_line_ids,
        raw_word_refs=discard_word_refs,
        refs_by_line=refs_by_line,
        known_refs=known_refs,
        destination="discard bucket",
    )
    duplicate_discards = sorted(ref for ref in discarded if ref in owners)
    if duplicate_discards:
        rendered = [
            {**_ref_dict(ref), "already_assigned_to": owners[ref]}
            for ref in duplicate_discards
        ]
        raise ResegmentPlanError(
            f"Discard bucket duplicates assignments: {rendered}"
        )

    unassigned = sorted(known_refs - set(owners) - discarded)
    if unassigned:
        raise ResegmentPlanError(
            "Every word must be assigned or explicitly discarded; "
            f"unassigned words: {[_ref_dict(ref) for ref in unassigned]}"
        )

    discarded_labels = [
        label for ref in discarded for label in labels_by_ref.get(ref, ())
    ]
    protected_discarded_labels = [
        label
        for label in discarded_labels
        if str(_value(label, "validation_status", "NONE") or "NONE").upper()
        != "INVALID"
    ]
    if protected_discarded_labels and not allow_labeled_discard:
        refs = sorted(
            {_word_ref(label) for label in protected_discarded_labels}
        )
        raise ResegmentPlanError(
            "Discarding labeled words requires allow_labeled_discard=true; "
            f"labeled words: {[_ref_dict(ref) for ref in refs]}"
        )
    if discarded and not str(discard_reason or "").strip():
        raise ResegmentPlanError(
            "discard_reason is required when words are discarded"
        )

    return {
        "segments": normalized_segments,
        "discard": {
            "word_refs": [_ref_dict(ref) for ref in sorted(discarded)],
            "word_count": len(discarded),
            "label_count": len(discarded_labels),
            "reason": discard_reason,
        },
        "totals": {
            "source_words": len(source_words),
            "assigned_words": len(owners),
            "discarded_words": len(discarded),
            "source_labels": len(source_labels),
            "preserved_labels": len(source_labels) - len(discarded_labels),
            "discarded_labels": len(discarded_labels),
        },
    }


def _validate_segment_definitions(
    segments: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    normalized = []
    segment_keys: set[str] = set()
    for raw_segment in segments:
        segment_key = str(raw_segment.get("segment_key", "")).strip()
        if not segment_key:
            raise ResegmentPlanError("Every segment requires a segment_key")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", segment_key):
            raise ResegmentPlanError(
                "segment_key must be 1-64 URL-safe characters and start "
                "with a letter or number"
            )
        if segment_key in segment_keys:
            raise ResegmentPlanError(f"Duplicate segment_key: {segment_key}")
        segment_keys.add(segment_key)

        place_policy = (
            str(raw_segment.get("place_policy", "none")).strip().lower()
        )
        if place_policy not in {"inherit", "none"}:
            raise ResegmentPlanError(
                f"Unsupported place_policy for segment {segment_key}: {place_policy}"
            )
        normalized.append(
            {
                "segment_key": segment_key,
                "place_policy": place_policy,
                "z_index": int(raw_segment.get("z_index", 0)),
                "occluded_by": sorted(
                    {
                        str(value)
                        for value in raw_segment.get("occluded_by", ())
                    }
                ),
                "visible_regions": deepcopy(
                    raw_segment.get("visible_regions", [])
                ),
            }
        )
    if not normalized:
        raise ResegmentPlanError("At least one output segment is required")
    if len(normalized) > 23:
        raise ResegmentPlanError(
            "A re-segmentation can create at most 23 segments"
        )
    for segment in normalized:
        unknown = sorted(set(segment["occluded_by"]) - segment_keys)
        if unknown:
            raise ResegmentPlanError(
                f"Segment {segment['segment_key']} has unknown occluded_by values: "
                f"{unknown}"
            )
        if segment["segment_key"] in segment["occluded_by"]:
            raise ResegmentPlanError(
                f"Segment {segment['segment_key']} cannot occlude itself"
            )
    return normalized, segment_keys


def _ensure_contiguous_word_span(
    refs: set[WordRef],
    refs_by_line: Mapping[int, set[WordRef]],
    destination: str,
) -> None:
    by_line: dict[int, list[int]] = defaultdict(list)
    for line_id, word_id in refs:
        by_line[line_id].append(word_id)
    for line_id, word_ids in by_line.items():
        ordered_source = sorted(
            word_id for _, word_id in refs_by_line[line_id]
        )
        positions = sorted(
            ordered_source.index(word_id) for word_id in word_ids
        )
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ResegmentPlanError(
                f"{destination} must be a contiguous word span on line {line_id}"
            )


def normalize_line_resegmentation_plan(
    *,
    lines: Iterable[Any],
    words: Iterable[Any],
    labels: Iterable[Any],
    segments: Iterable[Mapping[str, Any]],
    assignments: Mapping[str, Any],
    letters: Iterable[Any] = (),
) -> dict[str, Any]:
    """Normalize a v2 line-first plan with sparse mixed-line word overrides.

    Every source line receives one default destination (an output segment or
    discard). Words inherit that destination unless explicitly moved by a
    contiguous word override. Letters are intentionally not assignable: they
    always follow their parent word during apply.
    """
    source_lines = list(lines)
    source_words = list(words)
    source_letters = list(letters)
    source_labels = list(labels)
    if not source_lines:
        raise ResegmentPlanError("The source receipt has no lines")
    if not source_words:
        raise ResegmentPlanError("The source receipt has no words")

    known_line_ids = {_line_id(line) for line in source_lines}
    if len(known_line_ids) != len(source_lines):
        raise ResegmentPlanError(
            "The source receipt contains duplicate line keys"
        )
    known_refs = {_word_ref(word) for word in source_words}
    if len(known_refs) != len(source_words):
        raise ResegmentPlanError(
            "The source receipt contains duplicate word keys"
        )

    refs_by_line: dict[int, set[WordRef]] = defaultdict(set)
    for ref in known_refs:
        if ref[0] not in known_line_ids:
            raise ResegmentPlanError(
                f"The source receipt contains a word for unknown line {ref[0]}"
            )
        refs_by_line[ref[0]].add(ref)

    known_letter_refs = {_letter_ref(letter) for letter in source_letters}
    if len(known_letter_refs) != len(source_letters):
        raise ResegmentPlanError(
            "The source receipt contains duplicate letter keys"
        )
    orphan_letter_refs = sorted(
        letter_ref
        for letter_ref in known_letter_refs
        if letter_ref[:2] not in known_refs
    )
    if orphan_letter_refs:
        raise ResegmentPlanError(
            "The source receipt contains letters without matching words: "
            f"{orphan_letter_refs}"
        )
    letter_counts_by_ref = Counter(
        letter_ref[:2] for letter_ref in known_letter_refs
    )

    normalized_segments, segment_keys = _validate_segment_definitions(segments)
    default_owner_by_line: dict[int, str] = {}
    canonical_line_assignments = []
    for raw_assignment in assignments.get("lines", ()):
        line_id = int(raw_assignment["line_id"])
        segment_key = str(raw_assignment.get("segment_key", "")).strip()
        if line_id not in known_line_ids:
            raise ResegmentPlanError(
                f"Line assignment references unknown line {line_id}"
            )
        if segment_key not in segment_keys:
            raise ResegmentPlanError(
                f"Line {line_id} references unknown segment {segment_key}"
            )
        if line_id in default_owner_by_line:
            raise ResegmentPlanError(
                f"Line {line_id} has duplicate assignments"
            )
        default_owner_by_line[line_id] = segment_key
        canonical_line_assignments.append(
            {"line_id": line_id, "segment_key": segment_key}
        )

    discarded_line_ids = {
        int(value) for value in assignments.get("discard_lines", ())
    }
    unknown_discard_lines = sorted(discarded_line_ids - known_line_ids)
    if unknown_discard_lines:
        raise ResegmentPlanError(
            f"Discard bucket references unknown line_ids: {unknown_discard_lines}"
        )
    duplicate_lines = sorted(discarded_line_ids & set(default_owner_by_line))
    if duplicate_lines:
        raise ResegmentPlanError(
            f"Lines cannot be both assigned and discarded: {duplicate_lines}"
        )
    unassigned_lines = sorted(
        known_line_ids - set(default_owner_by_line) - discarded_line_ids
    )
    if unassigned_lines:
        raise ResegmentPlanError(
            "Every line must have one default destination; unassigned lines: "
            f"{unassigned_lines}"
        )
    wordless_assigned = sorted(
        line_id
        for line_id in default_owner_by_line
        if not refs_by_line.get(line_id)
    )
    if wordless_assigned:
        raise ResegmentPlanError(
            "Lines without words cannot be assigned to a segment because "
            "apply rebuilds outputs from words and would silently drop "
            "them; discard these lines explicitly: "
            f"{wordless_assigned}"
        )

    owners: dict[WordRef, str | None] = {}
    for ref in known_refs:
        owners[ref] = default_owner_by_line.get(ref[0])

    canonical_overrides = []
    overridden: set[WordRef] = set()
    for raw_override in assignments.get("words", ()):
        segment_key = str(raw_override.get("segment_key", "")).strip()
        if segment_key not in segment_keys:
            raise ResegmentPlanError(
                f"Word override references unknown segment {segment_key}"
            )
        reason = str(raw_override.get("reason", "")).strip()
        if not reason:
            raise ResegmentPlanError("Every word override requires a reason")
        refs = _parse_word_refs([raw_override])
        unknown_refs = sorted(refs - known_refs)
        if unknown_refs:
            raise ResegmentPlanError(
                f"Word override references unknown words: "
                f"{[_ref_dict(ref) for ref in unknown_refs]}"
            )
        _ensure_contiguous_word_span(refs, refs_by_line, "Word override")
        duplicates = sorted(refs & overridden)
        if duplicates:
            raise ResegmentPlanError(
                f"Words have duplicate overrides: "
                f"{[_ref_dict(ref) for ref in duplicates]}"
            )
        redundant = sorted(ref for ref in refs if owners[ref] == segment_key)
        if redundant:
            raise ResegmentPlanError(
                f"Word overrides must change the line default: "
                f"{[_ref_dict(ref) for ref in redundant]}"
            )
        for ref in refs:
            owners[ref] = segment_key
        overridden.update(refs)
        canonical_overrides.append(
            {
                "line_id": next(iter(refs))[0],
                "word_ids": sorted(ref[1] for ref in refs),
                "segment_key": segment_key,
                "reason": reason,
            }
        )

    discarded_word_refs = _parse_word_refs(
        assignments.get("discard_words", ())
    )
    unknown_discard_refs = sorted(discarded_word_refs - known_refs)
    if unknown_discard_refs:
        raise ResegmentPlanError(
            f"Discard bucket references unknown words: "
            f"{[_ref_dict(ref) for ref in unknown_discard_refs]}"
        )
    _ensure_contiguous_word_span(
        discarded_word_refs, refs_by_line, "Discard word override"
    )
    duplicate_discard_overrides = sorted(discarded_word_refs & overridden)
    if duplicate_discard_overrides:
        raise ResegmentPlanError(
            f"Words cannot be both overridden and discarded: "
            f"{[_ref_dict(ref) for ref in duplicate_discard_overrides]}"
        )
    redundant_discards = sorted(
        ref for ref in discarded_word_refs if owners[ref] is None
    )
    if redundant_discards:
        raise ResegmentPlanError(
            f"Discard word overrides must change the line default: "
            f"{[_ref_dict(ref) for ref in redundant_discards]}"
        )
    for ref in discarded_word_refs:
        owners[ref] = None

    labels_by_ref = _labels_by_ref(source_labels)
    orphan_label_refs = sorted(set(labels_by_ref) - known_refs)
    if orphan_label_refs:
        raise ResegmentPlanError(
            "The source receipt contains labels without matching words: "
            f"{[_ref_dict(ref) for ref in orphan_label_refs]}"
        )
    discarded = {ref for ref, owner in owners.items() if owner is None}
    discarded_labels = [
        label for ref in discarded for label in labels_by_ref.get(ref, ())
    ]
    protected_discarded_labels = [
        label
        for label in discarded_labels
        if str(_value(label, "validation_status", "NONE") or "NONE").upper()
        != "INVALID"
    ]
    allow_labeled_discard = bool(
        assignments.get("allow_labeled_discard", False)
    )
    if protected_discarded_labels and not allow_labeled_discard:
        refs = sorted(
            {_word_ref(label) for label in protected_discarded_labels}
        )
        raise ResegmentPlanError(
            "Discarding labeled words requires allow_labeled_discard=true; "
            f"labeled words: {[_ref_dict(ref) for ref in refs]}"
        )
    discard_reason = str(assignments.get("discard_reason", "")).strip() or None
    if discarded and not discard_reason:
        raise ResegmentPlanError(
            "discard_reason is required when words are discarded"
        )

    destinations_by_line: dict[int, set[str | None]] = defaultdict(set)
    for ref, owner in owners.items():
        destinations_by_line[ref[0]].add(owner)
    mixed_line_ids = sorted(
        line_id
        for line_id, destinations in destinations_by_line.items()
        if len(destinations) > 1
    )

    segment_by_key = {
        segment["segment_key"]: segment for segment in normalized_segments
    }
    for segment_key, segment in segment_by_key.items():
        selected = {
            ref for ref, owner in owners.items() if owner == segment_key
        }
        status_counts = Counter(
            str(_value(label, "validation_status", "NONE") or "NONE")
            for ref in selected
            for label in labels_by_ref.get(ref, ())
        )
        segment.update(
            {
                "line_ids": sorted(
                    line_id
                    for line_id, owner in default_owner_by_line.items()
                    if owner == segment_key
                ),
                "mixed_line_ids": sorted(
                    set(mixed_line_ids) & {ref[0] for ref in selected}
                ),
                "word_refs": [_ref_dict(ref) for ref in sorted(selected)],
                "word_count": len(selected),
                "letter_count": sum(
                    letter_counts_by_ref[ref] for ref in selected
                ),
                "label_count": sum(status_counts.values()),
                "label_status_counts": dict(sorted(status_counts.items())),
            }
        )
        if not selected:
            raise ResegmentPlanError(
                f"Segment {segment_key} does not select any words"
            )

    return {
        "assignment_mode": "LINE_FIRST",
        "segments": normalized_segments,
        "assignments": {
            "lines": sorted(
                canonical_line_assignments, key=lambda item: item["line_id"]
            ),
            "words": sorted(
                canonical_overrides,
                key=lambda item: (item["line_id"], item["word_ids"]),
            ),
            "discard_lines": sorted(discarded_line_ids),
            "discard_words": [
                _ref_dict(ref) for ref in sorted(discarded_word_refs)
            ],
            "discard_reason": discard_reason,
            "allow_labeled_discard": allow_labeled_discard,
        },
        "discard": {
            "line_ids": sorted(discarded_line_ids),
            "word_refs": [_ref_dict(ref) for ref in sorted(discarded)],
            "word_count": len(discarded),
            "letter_count": sum(
                letter_counts_by_ref[ref] for ref in discarded
            ),
            "label_count": len(discarded_labels),
            "reason": discard_reason,
        },
        "totals": {
            "source_lines": len(source_lines),
            "mixed_lines": len(mixed_line_ids),
            "source_words": len(source_words),
            "assigned_words": len(source_words) - len(discarded),
            "discarded_words": len(discarded),
            "source_letters": len(source_letters),
            "assigned_letters": sum(
                letter_counts_by_ref[ref]
                for ref, owner in owners.items()
                if owner
            ),
            "discarded_letters": sum(
                letter_counts_by_ref[ref] for ref in discarded
            ),
            "source_labels": len(source_labels),
            "preserved_labels": len(source_labels) - len(discarded_labels),
            "discarded_labels": len(discarded_labels),
        },
    }


def _point(entity: Any, name: str) -> Any:
    value = _value(entity, name)
    if not isinstance(value, Mapping):
        return value
    return {key: float(value[key]) for key in sorted(value)}


def _entity_payload(entity: Any) -> Any:
    if entity is None:
        return None
    if isinstance(entity, Mapping):
        return deepcopy(dict(entity))
    to_item = getattr(entity, "to_item", None)
    if callable(to_item):
        return to_item()
    return {
        key: value
        for key, value in vars(entity).items()
        if not key.startswith("_")
    }


def build_source_fingerprint(
    *,
    receipt: Any,
    words: Iterable[Any],
    labels: Iterable[Any],
    place: Any = None,
    image: Any = None,
    lines: Iterable[Any] = (),
    letters: Iterable[Any] = (),
    source_object: Mapping[str, Any] | None = None,
    source_type_counts: Mapping[str, Any] | None = None,
) -> str:
    """Hash all source fields whose change could invalidate a plan."""
    receipt_payload = {
        name: _value(receipt, name)
        for name in (
            "image_id",
            "receipt_id",
            "width",
            "height",
            "timestamp_added",
            "raw_s3_bucket",
            "raw_s3_key",
            "sha256",
        )
    }
    for name in ("top_left", "top_right", "bottom_left", "bottom_right"):
        receipt_payload[name] = _point(receipt, name)

    word_payload = []
    for word in sorted(words, key=_word_ref):
        item = {
            "line_id": _word_ref(word)[0],
            "word_id": _word_ref(word)[1],
            "text": _value(word, "text"),
            "confidence": _value(word, "confidence"),
            "is_noise": bool(_value(word, "is_noise", False)),
        }
        for name in (
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ):
            item[name] = _point(word, name)
        word_payload.append(item)

    label_payload = []
    for label in sorted(
        labels,
        key=lambda item: (*_word_ref(item), str(_value(item, "label", ""))),
    ):
        label_payload.append(
            {
                "line_id": _word_ref(label)[0],
                "word_id": _word_ref(label)[1],
                "label": _value(label, "label"),
                "validation_status": _value(label, "validation_status"),
                "reasoning": _value(label, "reasoning"),
                "timestamp_added": _value(label, "timestamp_added"),
                "label_proposed_by": _value(label, "label_proposed_by"),
                "label_consolidated_from": _value(
                    label, "label_consolidated_from"
                ),
            }
        )

    payload = {
        "receipt": receipt_payload,
        "words": word_payload,
        "labels": label_payload,
        "place": _entity_payload(place),
    }
    if image is not None or source_object is not None:
        payload.update(
            {
                "fingerprint_version": 2,
                "image": _entity_payload(image),
                "lines": [
                    _entity_payload(line)
                    for line in sorted(lines, key=_line_id)
                ],
                "letters": [
                    _entity_payload(letter)
                    for letter in sorted(letters, key=_letter_ref)
                ],
                "source_object": deepcopy(dict(source_object or {})),
                "source_type_counts": dict(
                    sorted((source_type_counts or {}).items())
                ),
            }
        )
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_plan_hash(plan: Mapping[str, Any]) -> str:
    """Return a stable hash while excluding mutable delivery metadata."""
    payload = deepcopy(dict(plan))
    for key in ("plan_hash", "preview_urls", "delivery", "status", "result"):
        payload.pop(key, None)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
