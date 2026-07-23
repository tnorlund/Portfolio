"""Unit tests for the systemic render fixes (fix/render-systemic).

Covers the label-hygiene and logo-band rules that erased real content:
INVALID labels feeding the renderer, the wordmark cluster absorbing
address words, and phrase anchors firing on substrings of unrelated words
("TREE" in "STREET").
"""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from scripts import render_synthetic_receipts as rsr  # noqa: E402


def _word(text, line_id, word_id, bbox):
    return {
        "receipt_id": 1,
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "bounding_box": {
            "x": bbox[0],
            "y": bbox[1],
            "width": bbox[2] - bbox[0],
            "height": bbox[3] - bbox[1],
        },
    }


def _label(label, line_id, word_id, status):
    return {
        "receipt_id": 1,
        "line_id": line_id,
        "word_id": word_id,
        "label": label,
        "validation_status": status,
    }


class TestRealReceiptDictLabelFilter:
    def _export(self, status):
        return {
            "receipt_words": [_word("WESTLAKE", 2, 1, [0.1, 0.1, 0.4, 0.15])],
            "receipt_word_labels": [_label("MERCHANT_NAME", 2, 1, status)],
        }

    def test_invalid_labels_are_excluded(self):
        real = rsr._real_receipt_dict(self._export("INVALID"), 1)
        assert real["words"][0]["labels"] == []

    def test_valid_labels_are_kept(self):
        real = rsr._real_receipt_dict(self._export("VALID"), 1)
        assert real["words"][0]["labels"] == ["MERCHANT_NAME"]

    def test_unreviewed_labels_are_kept(self):
        for status in (None, "", "PENDING", "NEEDS_REVIEW"):
            real = rsr._real_receipt_dict(self._export(status), 1)
            assert real["words"][0]["labels"] == ["MERCHANT_NAME"], status


class TestLogoWordmarkBounds:
    """The wordmark cluster must never absorb non-MERCHANT_NAME content."""

    def _receipt(self, extra_labels):
        # Receipt coords are y-up (larger y = higher on the paper): the brand
        # line sits at the very top with an adjacent word in the same column
        # one line below -- exactly the geometry the greedy absorption accepts.
        return {
            "words": [
                {
                    "text": "GELSONS",
                    "bbox": [100.0, 950.0, 500.0, 990.0],
                    "labels": ["MERCHANT_NAME"],
                },
                {
                    "text": "WESTLAKE",
                    "bbox": [120.0, 900.0, 400.0, 940.0],
                    "labels": extra_labels,
                },
            ]
        }

    def test_pure_merchant_name_word_is_absorbed(self):
        cluster, bbox = rsr._logo_wordmark_words(
            self._receipt(["MERCHANT_NAME"])
        )
        assert len(cluster) == 2
        assert bbox[1] == 900.0

    def test_word_with_address_label_is_not_absorbed(self):
        cluster, bbox = rsr._logo_wordmark_words(
            self._receipt(["MERCHANT_NAME", "ADDRESS_LINE"])
        )
        assert len(cluster) == 1
        assert bbox[1] == 950.0

    def test_word_with_only_address_label_is_not_absorbed(self):
        cluster, _ = rsr._logo_wordmark_words(self._receipt(["ADDRESS_LINE"]))
        assert len(cluster) == 1


class TestPhraseRunMatch:
    def test_multiword_slogan_assembles_from_adjacent_tokens(self):
        assert rsr._phrase_run_match(
            ["HOW", "DOERS", "GET", "MORE", "DONE"], ["HOWDOERSGETMOREDONE"]
        )

    def test_substring_of_one_token_does_not_match(self):
        assert not rsr._phrase_run_match(
            ["361", "WESTLAKE", "STREET"], ["TREE"]
        )

    def test_whole_token_matches(self):
        assert rsr._phrase_run_match(["DOLLAR", "TREE"], ["TREE"])

    def test_non_adjacent_tokens_do_not_match(self):
        assert not rsr._phrase_run_match(["HOW", "X", "DOERS"], ["HOWDOERS"])


class TestMeasuredSeparatorInventory:
    def test_empty_ocr_inventory_remains_authoritative(self):
        profile = {"layout_template": {"separators": []}}

        assert (
            rsr._measured_separator_inventory(profile, compose_kind=None) == ()
        )

    def test_empty_composed_inventory_preserves_canonical_output(self):
        profile = {
            "compose": "dollartree",
            "layout_template": {"separators": []},
        }

        assert (
            rsr._measured_separator_inventory(
                profile,
                compose_kind="dollartree",
            )
            is None
        )

    def test_measured_rows_still_apply_to_composed_layout(self):
        row = {"char": "*", "pos_frac_med": 0.75}
        profile = {
            "compose": "canonical",
            "layout_template": {"separators": [row]},
        }

        inventory = rsr._measured_separator_inventory(
            profile,
            compose_kind="canonical",
        )

        assert inventory == (row,)
        assert inventory[0] is not row


class TestMeasuredLayoutTemplate:
    def test_ocr_layout_receives_a_copy(self):
        template = {"columns": {"items": [{"role": "amount", "x": 0.8}]}}
        profile = {"layout_template": template}

        selected = rsr._measured_layout_template(
            profile,
            compose_kind=None,
        )

        assert selected == template
        assert selected is not template

    def test_composer_owns_output_geometry(self):
        profile = {
            "compose": "dollartree",
            "layout_template": {"columns": {"items": []}},
        }

        assert (
            rsr._measured_layout_template(
                profile,
                compose_kind="dollartree",
            )
            is None
        )

    def test_missing_layout_remains_a_noop(self):
        assert rsr._measured_layout_template({}, compose_kind=None) is None


class TestWordmarkSeedFilter:
    """P1 review finding: an UNLABELED word on the brand line (e.g. its label
    was INVALID-filtered) must not be seeded into the wordmark cluster."""

    def test_unlabeled_same_row_address_word_is_not_seeded(self):
        receipt = {
            "merchant_name": "Gelson's Westlake Village",
            "words": [
                {
                    "text": "GELSONS",
                    "bbox": [100.0, 950.0, 400.0, 990.0],
                    "labels": ["MERCHANT_NAME"],
                },
                # Same OCR row; its INVALID MERCHANT_NAME label was filtered.
                {
                    "text": "WESTLAKE",
                    "bbox": [420.0, 950.0, 700.0, 990.0],
                    "labels": [],
                },
            ],
        }
        cluster, bbox = rsr._logo_wordmark_words(receipt)
        assert [w["text"] for w in cluster] == ["GELSONS"]
        assert bbox[2] == 400.0

    def test_text_detected_brand_line_still_seeds_brand_words(self):
        # No labels at all: the Sprouts-style text-detected wordmark keeps
        # words whose text is part of the merchant name.
        receipt = {
            "merchant_name": "Sprouts Farmers Market",
            "words": [
                {
                    "text": "SPROUTS",
                    "bbox": [100.0, 950.0, 400.0, 990.0],
                    "labels": [],
                },
                {
                    "text": "FARMERS",
                    "bbox": [420.0, 950.0, 700.0, 990.0],
                    "labels": [],
                },
            ],
        }
        cluster, _ = rsr._logo_wordmark_words(receipt)
        assert {w["text"] for w in cluster} == {"SPROUTS", "FARMERS"}
