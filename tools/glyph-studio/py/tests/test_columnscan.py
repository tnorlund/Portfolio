"""Unit tests for the columnscan riders + layout_template (#1188 P2)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from glyphstudio.layout_template import (  # noqa: E402
    build_layout_template,
    measure_section_sequence,
    measure_separators,
)
from glyphstudio.styleagg import (  # noqa: E402
    pool_columns,
    receipt_section_columns,
)
from glyphstudio.stylescan import line_token_records  # noqa: E402


def w(text, l, r):
    return {"text": text, "l": l, "r": r, "t": 0, "b": 10}


def test_token_roles_and_weight_subline():
    tokens, ws = line_token_records(
        [
            w("MILK", 10, 60),
            w("2", 300, 310),
            w("4.99", 500, 560),
            w("F", 590, 600),
        ],
        "items",
        1000.0,
    )
    assert not ws
    assert [t["role"] for t in tokens] == ["desc", "qty", "amount", "flag"]
    # label rows: alpha tokens are labels, not descriptions
    tokens, _ = line_token_records(
        [w("SUB", 300, 340), w("TOTAL", 350, 420), w("6.25", 800, 860)],
        "summary",
        1000.0,
    )
    assert [t["role"] for t in tokens] == ["label", "label", "amount"]
    # weight sublines are excluded wholesale
    tokens, ws = line_token_records(
        [
            w("1.06", 100, 140),
            w("lb", 150, 170),
            w("@", 180, 190),
            w("8.99/lb", 200, 260),
        ],
        "items",
        1000.0,
    )
    assert ws and tokens == []


def _item_line(y, price_r=0.86):
    scale = 1000.0
    return {
        "section": "item",
        "section_canonical": "items",
        "tokens": [
            {"role": "desc", "l": 0.02, "r": 0.30},
            {"role": "amount", "l": price_r - 0.06, "r": price_r},
        ],
    }


def test_receipt_columns_cluster_and_support():
    lines = [_item_line(i) for i in range(6)]
    lines.append(_item_line(9, price_r=0.60))  # lone off-lane amount
    cols = receipt_section_columns(lines)
    items = cols["items"]
    amounts = [c for c in items if c["role"] == "amount"]
    # singletons are KEPT at receipt level (a one-row total_line lane is
    # legitimate); the cross-receipt pooling majority filters noise
    assert len(amounts) == 2
    main = max(amounts, key=lambda c: c["support"])
    assert abs(main["x"] - 0.86) < 1e-6
    assert main["support"] == 6
    lone = min(amounts, key=lambda c: c["support"])
    assert lone["support"] == 1
    descs = [c for c in items if c["role"] == "desc"]
    assert descs and descs[0]["anchor"] == "left"


def test_singleton_total_line_lane_survives_pooling():
    # review F9: three receipts, each with ONE identical total_line amount;
    # dropping singletons per-receipt made this lane unlearnable
    def receipt():
        return {
            "total_line": [
                {
                    "role": "amount",
                    "anchor": "right",
                    "x": 0.91,
                    "spread": 0.0,
                    "support": 1,
                }
            ]
        }

    pooled = pool_columns([receipt(), receipt(), receipt()])
    assert pooled["total_line"][0]["support"] == 3


def test_pool_columns_requires_majority():
    a = {
        "items": [
            {
                "role": "amount",
                "anchor": "right",
                "x": 0.860,
                "spread": 0.0,
                "support": 5,
            }
        ]
    }
    b = {
        "items": [
            {
                "role": "amount",
                "anchor": "right",
                "x": 0.874,
                "spread": 0.0,
                "support": 4,
            }
        ]
    }
    c = {
        "items": [
            {
                "role": "amount",
                "anchor": "right",
                "x": 0.600,
                "spread": 0.0,
                "support": 3,
            }
        ]
    }
    pooled = pool_columns([a, b, c])
    lanes = pooled["items"]
    assert (
        len(lanes) == 1
    )  # 0.86/0.874 pool (within 0.03); 0.60 lacks majority
    assert lanes[0]["support"] == 2
    assert 0.86 <= lanes[0]["x"] <= 0.874


def _scan(sections, separator_after=None):
    lines = []
    for sec in sections:
        if separator_after and sec == separator_after:
            lines.append(
                {"section": sec, "section_canonical": sec, "tokens": []}
            )
            lines.append(
                {
                    "section": "separator",
                    "section_canonical": None,
                    "text": "*" * 20,
                }
            )
            continue
        lines.append({"section": sec, "section_canonical": sec, "tokens": []})
    return {"lines": lines}


def test_section_sequence_and_separators():
    scans = [
        _scan(
            ["storefront", "address", "items", "summary", "footer"], "summary"
        ),
        _scan(
            ["storefront", "address", "items", "summary", "footer"], "summary"
        ),
        _scan(["storefront", "items", "summary", "footer"], "summary"),
    ]
    seq = measure_section_sequence(scans)
    names = [s["name"] for s in seq]
    assert names[0] == "storefront" and names[-1] == "footer"
    assert names.index("items") < names.index("summary")
    assert all(s["support"] >= 2 for s in seq)
    seps = measure_separators(scans)
    assert len(seps) == 1
    assert seps[0]["char"] == "*"
    assert seps[0]["after_section"] == "summary"
    assert seps[0]["ordinal"] == 0
    assert seps[0]["support"] == 3


def test_build_layout_template_shape():
    scans = [
        {"lines": [_item_line(i) for i in range(4)]},
        {"lines": [_item_line(i) for i in range(4)]},
    ]
    t = build_layout_template(scans)
    assert t["version"] == 1
    assert t["measured"]["receipts"] == 2
    assert "items" in t["columns"]
    assert isinstance(t["sections"], list)
    assert isinstance(t["separators"], list)


def test_pool_columns_ceils_half_and_counts_receipts():
    lane = {
        "role": "amount",
        "anchor": "right",
        "x": 0.86,
        "spread": 0.0,
        "support": 5,
    }
    # 2 of 5 receipts is NOT "at least half": ceil(5/2)=3 required
    per_receipt = [
        {"items": [dict(lane)]},
        {"items": [dict(lane)]},
        {},
        {},
        {},
    ]
    assert pool_columns(per_receipt) == {}
    per_receipt[2] = {"items": [dict(lane, x=0.874)]}
    pooled = pool_columns(per_receipt)
    assert pooled["items"][0]["support"] == 3
    # one receipt with TWO same-role lanes near one pool counts ONCE
    double = {"items": [dict(lane, x=0.858), dict(lane, x=0.9)]}
    per_receipt = [double, {}, {}]
    assert pool_columns(per_receipt) == {}  # 1 receipt < need
