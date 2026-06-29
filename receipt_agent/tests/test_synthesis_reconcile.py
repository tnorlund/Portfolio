"""Tests for shared synthetic-candidate reconciliation passes."""

import importlib.util
from pathlib import Path


_RECONCILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "receipt_agent"
    / "agents"
    / "label_evaluator"
    / "synthesis_reconcile.py"
)
_SPEC = importlib.util.spec_from_file_location("synthesis_reconcile", _RECONCILE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
reconcile_candidate = _MODULE.reconcile_candidate


def _merchant_block_count(tags):
    return sum(1 for tag in tags if tag == "B-MERCHANT_NAME")


def test_reconcile_promotes_missing_merchant_header_from_top_name_line():
    tokens = ["TARGET", "T-1234", "MILK", "$4.99", "TOTAL", "$4.99"]
    bboxes = [
        [100, 950, 175, 975],
        [190, 950, 250, 975],
        [100, 900, 150, 925],
        [850, 900, 910, 925],
        [100, 850, 165, 875],
        [850, 850, 910, 875],
    ]
    tags = ["O", "O", "B-PRODUCT_NAME", "B-LINE_TOTAL", "O", "B-GRAND_TOTAL"]

    out_tokens, out_bboxes, out_tags = reconcile_candidate(
        tokens,
        bboxes,
        tags,
        merchant_name="Target",
    )

    assert out_tokens == tokens
    assert out_bboxes == bboxes
    assert _merchant_block_count(out_tags) == 1
    assert out_tags[0] == "B-MERCHANT_NAME"
    assert out_tags[3] == "B-LINE_TOTAL"
    assert out_tags[5] == "B-GRAND_TOTAL"


def test_reconcile_normalizes_store_name_alias_when_header_is_missing():
    tokens = ["VONS", "MILK", "$4.99", "TOTAL", "$4.99"]
    bboxes = [
        [100, 950, 160, 975],
        [100, 900, 150, 925],
        [850, 900, 910, 925],
        [100, 850, 165, 875],
        [850, 850, 910, 875],
    ]
    tags = ["B-STORE_NAME", "B-PRODUCT_NAME", "B-LINE_TOTAL", "O", "B-GRAND_TOTAL"]

    _, _, out_tags = reconcile_candidate(tokens, bboxes, tags, merchant_name="Vons")

    assert _merchant_block_count(out_tags) == 1
    assert out_tags[0] == "B-MERCHANT_NAME"


def test_reconcile_reemits_single_b_tag_for_fragmented_merchant_run():
    tokens = ["THE", "HOME", "DEPOT", "MILK", "$4.99", "TOTAL", "$4.99"]
    bboxes = [
        [100, 950, 145, 975],
        [160, 950, 220, 975],
        [235, 950, 305, 975],
        [100, 900, 150, 925],
        [850, 900, 910, 925],
        [100, 850, 165, 875],
        [850, 850, 910, 875],
    ]
    tags = [
        "B-MERCHANT_NAME",
        "B-MERCHANT_NAME",
        "B-MERCHANT_NAME",
        "B-PRODUCT_NAME",
        "B-LINE_TOTAL",
        "O",
        "B-GRAND_TOTAL",
    ]

    _, _, out_tags = reconcile_candidate(
        tokens,
        bboxes,
        tags,
        merchant_name="The Home Depot",
    )

    assert _merchant_block_count(out_tags) == 1
    assert out_tags[:3] == [
        "B-MERCHANT_NAME",
        "I-MERCHANT_NAME",
        "I-MERCHANT_NAME",
    ]
