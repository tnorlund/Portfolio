"""Unit tests for cluster-before-pool layout variants (W-G).

Two synthetic populations (register vs self-checkout) MUST separate; a
homogeneous population with content jitter must NOT split; a shuffled input
order must yield byte-identical output; every emitted template/variant must
pass the unchanged validate_layout_template.
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from glyphstudio.layout_template import (  # noqa: E402
    validate_layout_template,
)
from glyphstudio.variant_cluster import (  # noqa: E402
    build_variant_payload,
    cluster_signatures,
    receipt_key,
    receipt_signature,
    signature_distance,
)

_H = 2000


def _line(y_frac, raw, canonical, text, tokens):
    y = int(y_frac * _H)
    return {
        "section": raw,
        "section_canonical": canonical,
        "text": text,
        "tokens": tokens,
        "bbox": [50, y - 10, 950, y + 10],
    }


def _tok(role, left, right):
    return {"role": role, "l": round(left, 4), "r": round(right, 4)}


def _register_scan(i):
    """Register-format receipt: member line, amounts at x~0.86, footer +
    barcode trailer. Content jitter: item count and lane wobble vary."""
    jx = 0.004 * ((i % 3) - 1)
    lines = [
        _line(0.02, "warehouse_header", "storefront", "COSTCO WHOLESALE", []),
        _line(0.05, "member", "storefront", "Member 111222333444", []),
        _line(0.08, "separator", None, "*" * 12, []),
    ]
    y = 0.14
    for n in range(5 + (i % 3)):
        lines.append(
            _line(
                y,
                "item",
                "items",
                f"ITEM {n} 9.99",
                [
                    _tok("desc", 0.05 + jx, 0.30),
                    _tok("amount", 0.80, 0.86 + jx),
                ],
            )
        )
        y += 0.05
    lines += [
        _line(
            0.55,
            "summary",
            "summary",
            "SUBTOTAL 59.94",
            [_tok("label", 0.40 + jx, 0.52), _tok("amount", 0.80, 0.86 + jx)],
        ),
        _line(
            0.58,
            "summary",
            "summary",
            "TAX 4.94",
            [_tok("label", 0.40 + jx, 0.46), _tok("amount", 0.80, 0.86 + jx)],
        ),
        _line(
            0.62,
            "total_line",
            "total_line",
            "**** TOTAL 64.88",
            [_tok("label", 0.35, 0.50), _tok("amount", 0.80, 0.86 + jx)],
        ),
        _line(0.70, "payment", "payment", "XXXXXXXXXXXX1234 CHIP", []),
        _line(
            0.75,
            "items_sold",
            "summary",
            "ITEMS SOLD 6",
            [_tok("label", 0.30, 0.48), _tok("amount", 0.80, 0.86 + jx)],
        ),
        _line(0.85, "footer", "footer", "OP# 12 THANK YOU", []),
    ]
    if i % 4 != 3:  # one register receipt lacks the barcode trailer
        lines.append(
            _line(0.92, "barcode_caption", "barcode", "1234567890123", [])
        )
    return {
        "image_id": f"reg-{i:04d}",
        "receipt_id": 1,
        "image_size": [1000, _H],
        "lines": lines,
    }


def _selfcheckout_scan(i):
    """Self-checkout format: SELF-CHECKOUT lane header, no member line, no
    footer/barcode trailer, amounts in a narrower lane at x~0.70."""
    jx = 0.004 * ((i % 3) - 1)
    lines = [
        _line(0.02, "self_checkout", "storefront", "SELF-CHECKOUT", []),
        _line(0.05, "warehouse_header", "storefront", "COSTCO WHOLESALE", []),
    ]
    y = 0.12
    for n in range(4 + (i % 2)):
        lines.append(
            _line(
                y,
                "item",
                "items",
                f"ITEM {n} 7.49",
                [
                    _tok("desc", 0.08 + jx, 0.32),
                    _tok("amount", 0.64, 0.70 + jx),
                ],
            )
        )
        y += 0.05
    lines += [
        _line(
            0.48,
            "summary",
            "summary",
            "SUBTOTAL 29.96",
            [_tok("label", 0.40 + jx, 0.52), _tok("amount", 0.64, 0.70 + jx)],
        ),
        _line(
            0.54,
            "total_line",
            "total_line",
            "**** TOTAL 32.43",
            [_tok("label", 0.35, 0.50), _tok("amount", 0.64, 0.70 + jx)],
        ),
        _line(
            0.60,
            "items_sold",
            "summary",
            "ITEMS SOLD 4",
            [_tok("label", 0.30, 0.48), _tok("amount", 0.64, 0.70 + jx)],
        ),
        _line(0.66, "payment", "payment", "XXXXXXXXXXXX1234 CHIP", []),
    ]
    return {
        "image_id": f"sfc-{i:04d}",
        "receipt_id": 1,
        "image_size": [1000, _H],
        "lines": lines,
    }


def _keyed(scans):
    return [(receipt_key(s["image_id"], s["receipt_id"]), s) for s in scans]


def _mixed_corpus():
    return _keyed(
        [_register_scan(i) for i in range(5)]
        + [_selfcheckout_scan(i) for i in range(4)]
    )


def test_signature_captures_markers_and_columns():
    sig = receipt_signature(_selfcheckout_scan(0))
    assert "self_checkout" in sig["markers"]
    assert "item" not in sig["markers"]  # content rows are not markers
    assert sig["sequence"][0] == "storefront"
    amounts = [
        c["x"] for c in sig["columns"]["items"] if c["role"] == "amount"
    ]
    assert amounts and abs(amounts[0] - 0.696) < 0.02


def test_within_population_distance_below_between():
    reg = [receipt_signature(_register_scan(i)) for i in range(3)]
    sfc = [receipt_signature(_selfcheckout_scan(i)) for i in range(3)]
    within = max(
        signature_distance(reg[0], reg[1]),
        signature_distance(reg[0], reg[2]),
        signature_distance(sfc[0], sfc[1]),
    )
    between = min(signature_distance(a, b) for a in reg for b in sfc)
    assert within < 0.18 < between


def test_two_populations_separate():
    payload = build_variant_payload(_mixed_corpus())
    template, verdict = payload["template"], payload["verdict"]
    assert verdict["status"] == "CONFIRM"
    assert len(verdict["clusters"]) == 2
    # dominant cluster = the 5 register receipts, top-level default
    assert template["support"] == 5
    assert all("reg-" in k for k in template["source_receipt_keys"])
    (variant,) = template["variants"]
    assert variant["support"] == 4
    assert all("sfc-" in k for k in variant["source_receipt_keys"])
    # the classifier hint names the self-checkout lane header marker
    assert variant["variant_id"] == "self_checkout"
    assert "self_checkout" in variant["classifier_hint"]["markers_any"]
    assert "member" in variant["classifier_hint"]["markers_absent"]
    # per-cluster pooling kept the distinct amount lanes
    default_amount = [
        c["x"] for c in template["columns"]["items"] if c["role"] == "amount"
    ]
    variant_amount = [
        c["x"] for c in variant["columns"]["items"] if c["role"] == "amount"
    ]
    assert abs(default_amount[0] - 0.86) < 0.02
    assert abs(variant_amount[0] - 0.70) < 0.02
    assert verdict["distinguishing_features"]


def test_homogeneous_population_does_not_split():
    payload = build_variant_payload(
        _keyed([_register_scan(i) for i in range(6)])
    )
    assert payload["verdict"]["status"] == "REFUTE"
    assert payload["template"]["variants"] == []
    assert payload["template"]["support"] == 6
    assert len(payload["verdict"]["clusters"]) == 1


def test_shuffled_input_is_deterministic():
    corpus = _mixed_corpus()
    shuffled = list(corpus)
    random.Random(42).shuffle(shuffled)
    a = build_variant_payload(corpus)
    b = build_variant_payload(shuffled)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_cluster_signatures_orders_are_stable():
    sigs = [
        receipt_signature(s)
        for _, s in sorted(_mixed_corpus(), key=lambda kv: kv[0])
    ]
    clusters = cluster_signatures(sigs)
    assert [len(c) for c in clusters] == [5, 4]
    assert clusters == [sorted(c) for c in clusters]


def test_emitted_payload_passes_existing_validator():
    payload = build_variant_payload(_mixed_corpus())
    assert validate_layout_template(payload["template"]) == []
    for variant in payload["template"]["variants"]:
        assert validate_layout_template({"version": 1, **variant}) == []


def test_photo_exclusion_recorded_in_provenance():
    excluded = [
        {
            "receipt_key": receipt_key("photo-0001", 1),
            "reason": "image_type=PHOTO",
        }
    ]
    payload = build_variant_payload(
        _mixed_corpus(), excluded_receipt_keys=excluded
    )
    assert payload["provenance"]["excluded_receipt_keys"] == excluded
    assert payload["provenance"]["source_receipt_keys"] == sorted(
        k for k, _ in _mixed_corpus()
    )


def test_singleton_outlier_not_emitted_as_variant():
    """An outlier receipt (unique layout) is reported, never a variant --
    pooling with support floor 2 would emit an empty template for it."""
    weird = _selfcheckout_scan(99)
    # push it far from both populations: extra-narrow lanes, no summary
    for line in weird["lines"]:
        for tok in line["tokens"]:
            tok["l"] = round(tok["l"] * 0.5, 4)
            tok["r"] = round(tok["r"] * 0.5, 4)
    weird["lines"] = [
        l for l in weird["lines"] if l["section"] != "items_sold"
    ]
    weird["image_id"] = "odd-0001"
    corpus = _keyed([_register_scan(i) for i in range(5)] + [weird])
    payload = build_variant_payload(corpus)
    assert payload["template"]["variants"] == []
    assert payload["verdict"]["status"] == "REFUTE"
    outlier_refs = [
        ref
        for out in payload["verdict"]["outliers"]
        for ref in out["receipt_refs"]
    ]
    assert outlier_refs == [receipt_key("odd-0001", 1)]


def _itemless_scan(i):
    """A cluster-forming scan whose item rows were swallowed by a content
    rule (the real Costco INSTANT-SAVINGS case): canonically there is no
    items section left, so its pooled template is unusable as a layout."""
    scan = _selfcheckout_scan(i)
    for line in scan["lines"]:
        if line["section"] == "item":
            line["section"] = "savings"
            line["section_canonical"] = "summary"
    scan["image_id"] = f"sav-{i:04d}"
    return scan


def test_degenerate_cluster_demoted_not_emitted():
    corpus = _keyed(
        [_register_scan(i) for i in range(5)]
        + [_itemless_scan(i) for i in range(2)]
    )
    payload = build_variant_payload(corpus)
    # the itemless pair clusters, but its pooled template lacks the items
    # section -> demoted to a DEGENERATE record, not a variant
    assert payload["template"]["variants"] == []
    assert payload["verdict"]["status"] == "REFUTE"
    (deg,) = payload["verdict"]["degenerate_clusters"]
    assert "items" in deg["reason"]
    assert deg["receipt_refs"] == [
        receipt_key("sav-0000", 1),
        receipt_key("sav-0001", 1),
    ]
    # verdict clusters carry marker prevalence for the owner's review
    assert (
        payload["verdict"]["clusters"][0]["marker_prevalence"]["member"] == 5
    )


def test_classifier_hint_markers_are_distinctive():
    """Markers shared by both populations must never become classifiers;
    only markers common to one side and rare on the other survive."""
    payload = build_variant_payload(_mixed_corpus())
    (variant,) = payload["template"]["variants"]
    hint = variant["classifier_hint"]
    for shared in (
        "warehouse_header",
        "items_sold",
        "summary",
        "total_line",
        "payment",
    ):
        assert shared not in hint["markers_any"]
        assert shared not in hint["markers_absent"]
    assert hint["markers_any"] == ["self_checkout"]
    assert set(hint["markers_absent"]) == {"member", "footer"}
