"""Unit tests for the label-move planner in the re-OCR apply tool.

Pure planning logic: no Docker, no AWS, no Swift. Exercises the policies that
matter: audit-trail carry, park-not-drop (NEEDS_REVIEW), pre-orphans untouched,
and repeated-word disambiguation by position.
"""

from __future__ import annotations

from scripts import ocr_migration_apply as app

IMG = "0e2f8a2c-1111-4222-8333-944445555666"


def w(text: str, x: float, y: float) -> dict:
    return {"text": text, "c": (x, y)}


def label_row(label: str, status: str = "VALID", **extra) -> dict:
    row = {
        "label": label,
        "validation_status": status,
        "label_proposed_by": "human_review",
        "reasoning": "looked right",
        "timestamp_added": "2026-01-01T00:00:00+00:00",
    }
    row.update(extra)
    return row


def test_exact_move_carries_everything():
    old_words = {(1, 1, 1): w("TOTAL", 0.5, 0.1)}
    old_labels = {(1, 1, 1): [label_row("GRAND_TOTAL")]}
    new_words = {(1, 3, 2): w("TOTAL", 0.5, 0.1)}
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    assert len(plan.moves) == 1 and not plan.parks and not plan.pre_orphaned
    mv = plan.moves[0]
    assert mv.new_key == (1, 3, 2)
    ent = app.moved_label_entity(IMG, mv)
    assert ent.receipt_id == 1 and ent.line_id == 3 and ent.word_id == 2
    assert ent.label == "GRAND_TOTAL"
    assert ent.validation_status == "VALID"
    assert ent.label_proposed_by == "human_review"
    assert ent.reasoning == "looked right"
    assert ent.timestamp_added == "2026-01-01T00:00:00+00:00"


def test_repeated_word_disambiguated_by_position():
    # Two identical "9.99" words; the labeled one is at the bottom. It must
    # match the bottom one in the new OCR, not the top.
    old_words = {
        (1, 2, 1): w("9.99", 0.8, 0.7),
        (1, 9, 1): w("9.99", 0.8, 0.1),  # labeled, near bottom
    }
    old_labels = {(1, 9, 1): [label_row("GRAND_TOTAL")]}
    new_words = {
        (1, 2, 1): w("9.99", 0.8, 0.7),
        (1, 10, 1): w("9.99", 0.8, 0.1),
    }
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    assert plan.moves[0].new_key == (1, 10, 1)


def test_reread_text_still_moves_by_position():
    old_words = {(1, 1, 1): w("$2.68", 0.5, 0.2)}
    old_labels = {(1, 1, 1): [label_row("LINE_TOTAL")]}
    new_words = {(1, 7, 2): w("$2.66", 0.5, 0.2)}  # re-read, same place
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    assert len(plan.moves) == 1 and plan.moves[0].new_key == (1, 7, 2)


def test_unmatched_label_parked_needs_review_never_dropped():
    old_words = {(1, 1, 1): w("SMOKESTACK", 0.5, 0.9)}
    old_labels = {(1, 1, 1): [label_row("PRODUCT_NAME", status="VALID")]}
    # New OCR read something totally different everywhere.
    new_words = {(1, 1, 1): w("ZZZZ", 0.1, 0.1)}
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    assert not plan.moves and len(plan.parks) == 1
    pk = plan.parks[0]
    assert pk.original_status == "VALID"
    ent = app.moved_label_entity(
        IMG,
        app.LabelMove(pk.old_key, pk.new_key, pk.native, pk.label),
        parked=True,
        old_word_text="SMOKESTACK",
    )
    assert ent.validation_status == "NEEDS_REVIEW"
    assert "PARKED by re-OCR migration" in ent.reasoning
    assert "SMOKESTACK" in ent.reasoning
    assert "VALID" in ent.reasoning  # original status recorded


def test_pre_orphaned_labels_left_untouched():
    old_words = {(1, 1, 1): w("A", 0.5, 0.5)}
    old_labels = {
        (1, 1, 1): [label_row("MERCHANT_NAME")],
        (1, 9, 9): [label_row("GRAND_TOTAL")],  # word (1,9,9) missing -> pre-orphan
    }
    new_words = {(1, 1, 1): w("A", 0.5, 0.5)}
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    assert len(plan.moves) == 1
    assert plan.pre_orphaned == [((1, 9, 9), "GRAND_TOTAL")]
    assert not plan.parks  # a pre-orphan is not parked, it is left alone


def test_multi_label_word_rides_together_and_split_receipts_correspond():
    # One word carries two labels; the image was re-segmented 1 receipt -> 2.
    old_words = {
        (1, 1, 1): w("COSTCO", 0.5, 0.95),
        (1, 8, 1): w("VISA", 0.5, 0.15),
    }
    old_labels = {
        (1, 1, 1): [label_row("MERCHANT_NAME"), label_row("BUSINESS_NAME")],
        (1, 8, 1): [label_row("PAYMENT_METHOD")],
    }
    new_words = {
        (1, 1, 1): w("COSTCO", 0.5, 0.95),  # new receipt 1
        (2, 1, 1): w("VISA", 0.5, 0.15),  # new receipt 2 (split)
    }
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    moved = {(m.label, m.new_key) for m in plan.moves}
    assert ("MERCHANT_NAME", (1, 1, 1)) in moved
    assert ("BUSINESS_NAME", (1, 1, 1)) in moved
    # old receipt 1 corresponds to ONE new receipt; VISA's label lands via the
    # fallback pass on the split receipt rather than being dropped.
    assert ("PAYMENT_METHOD", (2, 1, 1)) in moved
    assert not plan.parks


def test_centroid_handles_dynamodb_decimals():
    # Live Dynamo reads deserialize numbers as decimal.Decimal; the planner
    # must not blow up on Decimal / float (the first dry-run failed on this).
    from decimal import Decimal

    d = {
        "top_left": {"x": Decimal("0.2"), "y": Decimal("0.9")},
        "bottom_right": {"x": Decimal("0.4"), "y": Decimal("0.8")},
    }
    cx, cy = app._centroid(d)
    assert isinstance(cx, float) and isinstance(cy, float)
    assert abs(cx - 0.3) < 1e-9
    old_words = {(1, 1, 1): {"text": "TOTAL", "c": app._centroid(d)}}
    old_labels = {(1, 1, 1): [label_row("GRAND_TOTAL")]}
    new_words = {(1, 1, 1): {"text": "TOTAL", "c": (0.3, 0.85)}}
    plan = app.plan_label_moves(old_words, old_labels, new_words)
    assert len(plan.moves) == 1
