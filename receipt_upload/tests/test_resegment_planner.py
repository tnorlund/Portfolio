"""Regression tests for safe receipt re-segmentation planning."""

import pytest

from receipt_upload.resegment import (
    ResegmentPlanError,
    build_source_fingerprint,
    normalize_line_resegmentation_plan,
    normalize_resegmentation_plan,
)

TWIN_PEAKS_WORD_COUNTS = {
    1: 2,
    2: 3,
    3: 2,
    4: 1,
    5: 2,
    6: 2,
    7: 2,
    8: 3,
    9: 5,
    10: 4,
    11: 2,
    12: 2,
    13: 2,
    14: 2,
    15: 1,
    16: 1,
    17: 1,
    18: 1,
    19: 2,
    20: 1,
    21: 3,
    22: 2,
    23: 3,
    24: 2,
    25: 1,
    26: 1,
    27: 1,
    28: 1,
    29: 2,
    30: 1,
    31: 2,
    32: 1,
    33: 1,
    34: 4,
    35: 2,
    36: 3,
    37: 1,
    38: 1,
    39: 1,
    40: 1,
    41: 1,
    42: 1,
    43: 1,
    44: 4,
    45: 7,
    46: 5,
    47: 4,
    48: 4,
    49: 1,
    50: 1,
    51: 1,
    52: 4,
    53: 1,
    54: 1,
    55: 2,
    56: 7,
    57: 5,
    58: 5,
    59: 6,
}


def _word(line_id: int, word_id: int) -> dict:
    text_overrides = {
        (34, 1): "Appr'",
        (34, 2): "CP",
        (34, 3): "CREDIT",
        (34, 4): "#",
        (35, 1): "Entr",
        (35, 2): "Authorizing...",
        (36, 1): "Mod",
        (36, 2): "Balance",
        (36, 3): "Due",
    }
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text_overrides.get(
            (line_id, word_id), f"w{line_id}-{word_id}"
        ),
        "top_left": {"x": 0.1, "y": 0.9},
        "top_right": {"x": 0.2, "y": 0.9},
        "bottom_left": {"x": 0.1, "y": 0.8},
        "bottom_right": {"x": 0.2, "y": 0.8},
        "confidence": 1.0,
    }


def _twin_peaks_words() -> list[dict]:
    return [
        _word(line_id, word_id)
        for line_id, count in TWIN_PEAKS_WORD_COUNTS.items()
        for word_id in range(1, count + 1)
    ]


def _letters_for_words(words: list[dict], total: int) -> list[dict]:
    base, remainder = divmod(total, len(words))
    letters = []
    for index, word in enumerate(words):
        count = base + (1 if index < remainder else 0)
        letters.extend(
            {
                "line_id": word["line_id"],
                "word_id": word["word_id"],
                "letter_id": letter_id,
                "text": "x",
            }
            for letter_id in range(1, count + 1)
        )
    return letters


CARD_LINES = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    15,
    16,
    17,
    29,
    31,
    32,
    42,
    43,
    50,
    54,
    55,
    56,
    57,
    58,
    59,
]
GUEST_LINES = [
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    30,
    33,
    37,
    38,
    39,
    40,
    41,
    44,
    45,
    46,
    47,
    48,
    49,
]


def _segments() -> list[dict]:
    return [
        {
            "segment_key": "card-slip",
            "include_line_ids": CARD_LINES,
            "include_word_refs": [
                {"line_id": 34, "word_ids": [1]},
                {"line_id": 35, "word_ids": [1]},
                {"line_id": 36, "word_ids": [1]},
            ],
        },
        {
            "segment_key": "guest-check",
            "include_line_ids": GUEST_LINES,
            "include_word_refs": [
                {"line_id": 34, "word_ids": [2, 3, 4]},
                {"line_id": 35, "word_ids": [2]},
                {"line_id": 36, "word_ids": [2, 3]},
            ],
        },
    ]


def test_twin_peaks_plan_partitions_every_word_once():
    words = _twin_peaks_words()
    card_refs = [
        word
        for word in words
        if word["line_id"] in CARD_LINES
        or (word["line_id"] in {34, 35, 36} and word["word_id"] == 1)
    ]
    guest_refs = [
        word
        for word in words
        if word["line_id"] in GUEST_LINES
        or (word["line_id"] == 34 and word["word_id"] in {2, 3, 4})
        or (word["line_id"] == 35 and word["word_id"] == 2)
        or (word["line_id"] == 36 and word["word_id"] in {2, 3})
    ]
    discard_refs = [word for word in words if word["line_id"] in {51, 52, 53}]
    letters = [
        *_letters_for_words(card_refs, 253),
        *_letters_for_words(guest_refs, 361),
        *_letters_for_words(discard_refs, 18),
    ]
    labels = [
        {
            "line_id": word["line_id"],
            "word_id": word["word_id"],
            "label": "TEST_LABEL",
            "validation_status": "VALID",
        }
        for word in [*card_refs[:19], *guest_refs[:64]]
    ]

    lines = [
        {
            "line_id": line_id,
            "text": f"line {line_id}",
            "confidence": 1.0,
            "angle_degrees": 0.0,
        }
        for line_id in TWIN_PEAKS_WORD_COUNTS
    ]
    assignments = {
        "lines": [
            *[
                {"line_id": line_id, "segment_key": "card-slip"}
                for line_id in CARD_LINES
            ],
            *[
                {"line_id": line_id, "segment_key": "guest-check"}
                for line_id in [*GUEST_LINES, 34, 35, 36]
            ],
        ],
        "words": [
            {
                "line_id": line_id,
                "word_ids": [1],
                "segment_key": "card-slip",
                "reason": "Mixed OCR bridge line",
            }
            for line_id in (34, 35, 36)
        ],
        "discard_lines": [51, 52, 53],
        "discard_reason": "Unrelated menu text",
    }
    plan = normalize_line_resegmentation_plan(
        lines=lines,
        words=words,
        letters=letters,
        labels=labels,
        segments=[
            {
                "segment_key": "card-slip",
                "z_index": 0,
                "occluded_by": ["guest-check"],
            },
            {
                "segment_key": "guest-check",
                "z_index": 1,
                "occluded_by": [],
            },
        ],
        assignments=assignments,
    )

    assert plan["totals"] == {
        "source_lines": 59,
        "mixed_lines": 3,
        "source_words": 136,
        "assigned_words": 130,
        "discarded_words": 6,
        "source_letters": 632,
        "assigned_letters": 614,
        "discarded_letters": 18,
        "source_labels": 83,
        "preserved_labels": 83,
        "discarded_labels": 0,
    }
    assert [segment["word_count"] for segment in plan["segments"]] == [
        54,
        76,
    ]
    assert [segment["label_count"] for segment in plan["segments"]] == [19, 64]
    assert [segment["letter_count"] for segment in plan["segments"]] == [
        253,
        361,
    ]
    assert plan["discard"]["letter_count"] == 18
    assert plan["segments"][0]["mixed_line_ids"] == [34, 35, 36]


def test_duplicate_assignment_is_rejected():
    words = [_word(1, 1)]
    with pytest.raises(ResegmentPlanError, match="duplicates assignments"):
        normalize_resegmentation_plan(
            words=words,
            labels=[],
            segments=[
                {"segment_key": "a", "include_line_ids": [1]},
                {
                    "segment_key": "b",
                    "include_word_refs": [{"line_id": 1, "word_id": 1}],
                },
            ],
        )


def test_unassigned_word_is_rejected():
    with pytest.raises(ResegmentPlanError, match="unassigned words"):
        normalize_resegmentation_plan(
            words=[_word(1, 1), _word(2, 1)],
            labels=[],
            segments=[{"segment_key": "a", "include_line_ids": [1]}],
        )


def test_labeled_discard_requires_explicit_acknowledgement():
    words = [_word(1, 1), _word(2, 1)]
    labels = [
        {
            "line_id": 2,
            "word_id": 1,
            "label": "GRAND_TOTAL",
            "validation_status": "VALID",
        }
    ]
    with pytest.raises(ResegmentPlanError, match="labeled words"):
        normalize_resegmentation_plan(
            words=words,
            labels=labels,
            segments=[{"segment_key": "a", "include_line_ids": [1]}],
            discard_line_ids=[2],
            discard_reason="Not part of the receipt",
        )


def test_invalid_word_reference_and_place_policy_are_rejected():
    words = [_word(1, 1)]
    with pytest.raises(ResegmentPlanError, match="requires either"):
        normalize_resegmentation_plan(
            words=words,
            labels=[],
            segments=[
                {
                    "segment_key": "a",
                    "include_word_refs": [{"line_id": 1}],
                }
            ],
        )

    with pytest.raises(ResegmentPlanError, match="place_policy"):
        normalize_resegmentation_plan(
            words=words,
            labels=[],
            segments=[
                {
                    "segment_key": "a",
                    "include_line_ids": [1],
                    "place_policy": "guess",
                }
            ],
        )


def test_orphan_label_is_rejected_before_planning():
    with pytest.raises(ResegmentPlanError, match="without matching words"):
        normalize_resegmentation_plan(
            words=[_word(1, 1)],
            labels=[
                {
                    "line_id": 2,
                    "word_id": 1,
                    "label": "GRAND_TOTAL",
                    "validation_status": "VALID",
                }
            ],
            segments=[{"segment_key": "a", "include_line_ids": [1]}],
        )


def test_source_fingerprint_changes_with_word_or_label_state():
    receipt = {
        "image_id": "img",
        "receipt_id": 1,
        "width": 100,
        "height": 200,
        "top_left": {"x": 0.0, "y": 1.0},
        "top_right": {"x": 1.0, "y": 1.0},
        "bottom_left": {"x": 0.0, "y": 0.0},
        "bottom_right": {"x": 1.0, "y": 0.0},
    }
    words = [_word(1, 1)]
    labels = [
        {
            "line_id": 1,
            "word_id": 1,
            "label": "ADDRESS_LINE",
            "validation_status": "INVALID",
        }
    ]
    original = build_source_fingerprint(
        receipt=receipt, words=words, labels=labels
    )
    changed_words = [{**words[0], "text": "changed"}]
    changed_labels = [{**labels[0], "validation_status": "VALID"}]

    assert original != build_source_fingerprint(
        receipt=receipt, words=changed_words, labels=labels
    )
    assert original != build_source_fingerprint(
        receipt=receipt, words=words, labels=changed_labels
    )
    assert original != build_source_fingerprint(
        receipt=receipt,
        words=words,
        labels=labels,
        place={"place_id": "new-place"},
    )


def test_v2_source_fingerprint_covers_image_lines_letters_and_source_bytes():
    receipt = {
        "image_id": "img",
        "receipt_id": 1,
        "width": 100,
        "height": 200,
        "top_left": {"x": 0.0, "y": 1.0},
        "top_right": {"x": 1.0, "y": 1.0},
        "bottom_left": {"x": 0.0, "y": 0.0},
        "bottom_right": {"x": 1.0, "y": 0.0},
    }
    kwargs = {
        "receipt": receipt,
        "words": [_word(1, 1)],
        "labels": [],
        "image": {
            "image_id": "img",
            "image_type": "PHOTO",
            "width": 100,
            "height": 200,
        },
        "lines": [{"line_id": 1, "text": "CARD", "angle_degrees": 0.0}],
        "letters": [
            {
                "line_id": 1,
                "word_id": 1,
                "letter_id": 1,
                "text": "C",
            }
        ],
        "source_object": {"content_sha256": "abc", "version_id": "v1"},
        "source_type_counts": {"RECEIPT_LINE": 1, "RECEIPT_WORD": 1},
    }
    original = build_source_fingerprint(**kwargs)

    for field, replacement in (
        ("image", {**kwargs["image"], "image_type": "SCAN"}),
        ("lines", [{**kwargs["lines"][0], "text": "CHANGED"}]),
        ("letters", [{**kwargs["letters"][0], "text": "X"}]),
        ("source_object", {"content_sha256": "changed", "version_id": "v1"}),
        ("source_type_counts", {"RECEIPT_LINE": 2, "RECEIPT_WORD": 1}),
    ):
        changed = {**kwargs, field: replacement}
        assert build_source_fingerprint(**changed) != original


def test_line_first_plan_rejects_missing_lines_and_noncontiguous_overrides():
    lines = [{"line_id": 1}, {"line_id": 2}]
    words = [_word(1, word_id) for word_id in (1, 2, 3)] + [_word(2, 1)]
    segments = [{"segment_key": "a"}, {"segment_key": "b"}]

    with pytest.raises(ResegmentPlanError, match="unassigned lines"):
        normalize_line_resegmentation_plan(
            lines=lines,
            words=words,
            labels=[],
            segments=segments,
            assignments={
                "lines": [{"line_id": 1, "segment_key": "a"}],
            },
        )

    with pytest.raises(ResegmentPlanError, match="contiguous word span"):
        normalize_line_resegmentation_plan(
            lines=lines,
            words=words,
            labels=[],
            segments=segments,
            assignments={
                "lines": [
                    {"line_id": 1, "segment_key": "a"},
                    {"line_id": 2, "segment_key": "b"},
                ],
                "words": [
                    {
                        "line_id": 1,
                        "word_ids": [1, 3],
                        "segment_key": "b",
                        "reason": "invalid split",
                    }
                ],
            },
        )


def test_line_first_plan_rejects_wordless_line_assigned_to_segment():
    """Apply rebuilds outputs from words, so a wordless line assigned to a
    segment would be silently destroyed; it must be discarded explicitly."""
    lines = [{"line_id": 1}, {"line_id": 2}]
    words = [_word(1, 1)]
    segments = [{"segment_key": "a"}]

    with pytest.raises(ResegmentPlanError, match="Lines without words"):
        normalize_line_resegmentation_plan(
            lines=lines,
            words=words,
            labels=[],
            segments=segments,
            assignments={
                "lines": [
                    {"line_id": 1, "segment_key": "a"},
                    {"line_id": 2, "segment_key": "a"},
                ],
            },
        )

    plan = normalize_line_resegmentation_plan(
        lines=lines,
        words=words,
        labels=[],
        segments=segments,
        assignments={
            "lines": [{"line_id": 1, "segment_key": "a"}],
            "discard_lines": [2],
            "discard_reason": "empty OCR line",
        },
    )
    assert plan["totals"]["source_words"] == 1
