"""Unit tests for the systemic render fixes (fix/render-systemic).

Covers the label-hygiene and logo-band rules that erased real content:
INVALID labels feeding the renderer, the wordmark cluster absorbing
address words, and phrase anchors firing on substrings of unrelated words
("TREE" in "STREET").
"""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from receipt_agent.agents.label_evaluator.rendering import (  # noqa: E402
    receipt_graphics,
)
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


class TestInbodyBarcodePayload:
    """HRI-derived Code128 symbols must remain decodable and truthful."""

    PAYLOAD = "99022003402972471754"

    def test_code128_encoder_keeps_a_leading_99_data_pair(self):
        symbol = receipt_graphics._barcode_symbol(  # pylint: disable=protected-access
            self.PAYLOAD,
            "code128",
        )

        # Code128 Start C (105) followed by the first data pair (99). The
        # dependency's optimizer previously mistook that data pair for a
        # charset-switch opcode and deleted it.
        assert symbol.encoded[:2] == [105, 99]

    def test_sprouts_preserves_the_hri_as_the_encoded_payload(self):
        options = receipt_graphics.graphics_profile_for_merchant(
            "Sprouts Farmers Market"
        )["inbody_barcode"]

        assert (
            rsr._inbody_barcode_payload(  # pylint: disable=protected-access
                self.PAYLOAD,
                options,
            )
            == self.PAYLOAD
        )

    def test_vons_keeps_numeric_code128_for_scannable_modules(self):
        profile = receipt_graphics.graphics_profile_for_merchant("Vons")
        options = {
            **rsr._INBODY_BARCODE_DEFAULTS,
            **profile["inbody_barcode"],
        }

        payload = rsr._inbody_barcode_payload(
            "00313505101552502031820", options
        )

        assert payload == "00313505101552502031820"

    def test_other_inbody_barcodes_keep_the_density_shaping_default(self):
        assert (
            rsr._inbody_barcode_payload(  # pylint: disable=protected-access
                "12345678901234",
                {"symbology": "code128"},
            )
            == "12-34-56-78-90-12-34"
        )

    def test_sprouts_keeps_scanner_quiet_zones_around_the_symbol(self):
        options = receipt_graphics.graphics_profile_for_merchant(
            "Sprouts Farmers Market"
        )["inbody_barcode"]
        tile = receipt_graphics.render_barcode_tile(
            self.PAYLOAD,
            "code128",
            445,
            84,
            with_hri=False,
        )

        prepared = (
            rsr._fit_inbody_barcode_tile(  # pylint: disable=protected-access
                tile,
                445,
                84,
                options,
            ).convert("L")
        )

        assert prepared.crop((0, 0, 3, 84)).getextrema()[0] > 230
        assert prepared.crop((442, 0, 445, 84)).getextrema()[0] > 230
