"""Per-class held-out metrics must be emitted for every class a model can emit.

The regression these lock down: the held-out callback used to write
``heldout_label_*`` rows for a hardcoded list of four product-detail labels.
A model whose vocabulary excludes those four (the 8-class non-product model)
therefore recorded ZERO per-class held-out rows, leaving only aggregates that
are meaningless to compare across differing label sets.
"""

import pytest

from receipt_layoutlm.trainer import (
    entity_labels_from_label_list,
    heldout_per_label_metric_rows,
)

# The 22-class wide model's label list, BIO-prefixed, as stored in a
# checkpoint's config.json.
WIDE_LABELS = ["O"] + [
    f"{prefix}-{name}"
    for name in (
        "MERCHANT_NAME",
        "DATE",
        "TIME",
        "PRODUCT_NAME",
        "QUANTITY",
        "UNIT_PRICE",
        "LINE_TOTAL",
        "SUBTOTAL",
        "TAX",
        "GRAND_TOTAL",
        "PAYMENT_METHOD",
    )
    for prefix in ("B", "I")
]

# The 8-class non-product model: no PRODUCT_NAME/QUANTITY/UNIT_PRICE/LINE_TOTAL.
NONPRODUCT_LABELS = ["O"] + [
    f"{prefix}-{name}"
    for name in (
        "MERCHANT_NAME",
        "DATE",
        "TIME",
        "SUBTOTAL",
        "TAX",
        "GRAND_TOTAL",
        "PAYMENT_METHOD",
        "ADDRESS_LINE",
    )
    for prefix in ("B", "I")
]


def _entry(**per_label):
    """Build a synthetic epoch entry like evaluate_live_checkpoint returns."""
    return {
        "heldout_f1": 0.5,
        "per_label_f1": per_label.get("f1", {}),
        "per_label_precision": per_label.get("precision", {}),
        "per_label_recall": per_label.get("recall", {}),
        "per_label_support": per_label.get("support", {}),
    }


def _by_name(rows):
    return {name: (value, unit) for name, value, unit in rows}


def test_entity_labels_strip_bio_and_drop_o():
    assert entity_labels_from_label_list(NONPRODUCT_LABELS) == [
        "ADDRESS_LINE",
        "DATE",
        "GRAND_TOTAL",
        "MERCHANT_NAME",
        "PAYMENT_METHOD",
        "SUBTOTAL",
        "TAX",
        "TIME",
    ]


def test_entity_labels_handle_unprefixed_and_junk_entries():
    assert entity_labels_from_label_list(
        ["O", "DATE", "B-DATE", "I-DATE", None, 7, ""]
    ) == ["DATE"]


def test_emits_four_rows_for_every_class_in_the_label_map():
    """The 8-class model must produce 8 x 4 rows, where it produced 0 before."""
    entry = _entry(
        f1={"DATE": 0.9, "TAX": 0.5},
        precision={"DATE": 0.88, "TAX": 0.45},
        recall={"DATE": 0.92, "TAX": 0.55},
        support={"DATE": 110, "TAX": 64},
    )

    rows = heldout_per_label_metric_rows(entry, NONPRODUCT_LABELS)
    names = _by_name(rows)

    assert len(rows) == 8 * 4
    for label in entity_labels_from_label_list(NONPRODUCT_LABELS):
        for metric in ("f1", "precision", "recall", "support"):
            assert f"heldout_label_{label}_{metric}" in names


def test_scored_classes_keep_their_values_and_v30_units():
    entry = _entry(
        f1={"DATE": 0.9},
        precision={"DATE": 0.88},
        recall={"DATE": 0.92},
        support={"DATE": 110},
    )

    names = _by_name(heldout_per_label_metric_rows(entry, NONPRODUCT_LABELS))

    assert names["heldout_label_DATE_f1"] == (0.9, "ratio")
    assert names["heldout_label_DATE_precision"] == (0.88, "ratio")
    assert names["heldout_label_DATE_recall"] == (0.92, "ratio")
    assert names["heldout_label_DATE_support"] == (110.0, "count")


def test_zero_support_class_records_a_row_rather_than_vanishing():
    """A missing row and a zero score are different facts.

    seqeval's report omits a class entirely when it appears in neither gold
    nor predictions. That absence used to become silence; it must become an
    explicit support-0 row.
    """
    entry = _entry(
        f1={"DATE": 0.9},
        precision={"DATE": 0.88},
        recall={"DATE": 0.92},
        support={"DATE": 110},
    )

    names = _by_name(heldout_per_label_metric_rows(entry, NONPRODUCT_LABELS))

    # ADDRESS_LINE was never scored this epoch.
    assert names["heldout_label_ADDRESS_LINE_support"] == (0.0, "count")
    assert names["heldout_label_ADDRESS_LINE_f1"] == (0.0, "ratio")
    assert names["heldout_label_ADDRESS_LINE_precision"] == (0.0, "ratio")
    assert names["heldout_label_ADDRESS_LINE_recall"] == (0.0, "ratio")


def test_product_labels_still_emitted_for_the_wide_model():
    """v30-era rows must keep coming out with identical names and units."""
    entry = _entry(
        f1={"QUANTITY": 0.3988269794721408},
        precision={"QUANTITY": 0.41},
        recall={"QUANTITY": 0.39},
        support={"QUANTITY": 252},
    )

    names = _by_name(heldout_per_label_metric_rows(entry, WIDE_LABELS))

    assert len(names) == 11 * 4
    assert names["heldout_label_QUANTITY_f1"] == (
        0.3988269794721408,
        "ratio",
    )
    assert names["heldout_label_QUANTITY_support"] == (252.0, "count")
    for label in ("PRODUCT_NAME", "UNIT_PRICE", "LINE_TOTAL"):
        assert names[f"heldout_label_{label}_f1"] == (0.0, "ratio")
        assert names[f"heldout_label_{label}_support"] == (0.0, "count")


def test_names_and_units_match_the_rows_v30_actually_recorded():
    """Verified against the real JobMetric rows for
    ``layoutlm-v30-fullcore-clean-data-20260713-222017`` in dev
    ``ReceiptsTable-dc5be22`` (job e5a9a687-4ab9-4d1b-82de-01238f60b19b):
    44 epochs x these 16 names, f1/precision/recall in ``ratio``, support in
    ``count``. New runs must be readable side by side with those.
    """
    v30_recorded = {
        f"heldout_label_{label}_{metric}": (
            "count" if metric == "support" else "ratio"
        )
        for label in (
            "PRODUCT_NAME",
            "QUANTITY",
            "UNIT_PRICE",
            "LINE_TOTAL",
        )
        for metric in ("f1", "precision", "recall", "support")
    }

    emitted = {
        name: unit
        for name, _, unit in heldout_per_label_metric_rows(
            _entry(), WIDE_LABELS
        )
    }

    assert v30_recorded.items() <= emitted.items()


def test_seqeval_aggregate_rows_never_become_labels():
    """``micro avg`` is not a class and must not be emitted as one."""
    entry = _entry(
        f1={"micro avg": 0.56, "macro avg": 0.4, "weighted avg": 0.5},
        support={"micro avg": 900},
    )

    names = _by_name(heldout_per_label_metric_rows(entry, NONPRODUCT_LABELS))

    assert not any("avg" in name for name in names)

    # Even in the no-label-map fallback path.
    fallback = _by_name(heldout_per_label_metric_rows(entry, []))
    assert fallback == {}


def test_falls_back_to_scored_classes_when_label_map_is_unavailable():
    entry = _entry(
        f1={"DATE": 0.9, "micro avg": 0.5},
        support={"DATE": 110, "micro avg": 900},
    )

    names = _by_name(heldout_per_label_metric_rows(entry, None))

    assert set(names) == {
        "heldout_label_DATE_f1",
        "heldout_label_DATE_precision",
        "heldout_label_DATE_recall",
        "heldout_label_DATE_support",
    }


@pytest.mark.parametrize(
    "bad", [None, "n/a", True, float("nan"), float("inf")]
)
def test_unusable_scores_degrade_to_zero_not_a_missing_row(bad):
    """JobMetric rejects non-finite values, and a rejected write is silence."""
    entry = _entry(f1={"DATE": bad}, support={"DATE": bad})

    names = _by_name(heldout_per_label_metric_rows(entry, NONPRODUCT_LABELS))

    assert names["heldout_label_DATE_f1"] == (0.0, "ratio")
    assert names["heldout_label_DATE_support"] == (0.0, "count")


def test_rows_are_deterministically_ordered():
    entry = _entry()
    first = heldout_per_label_metric_rows(entry, NONPRODUCT_LABELS)
    second = heldout_per_label_metric_rows(
        entry, list(reversed(NONPRODUCT_LABELS))
    )
    assert [name for name, _, _ in first] == [name for name, _, _ in second]
