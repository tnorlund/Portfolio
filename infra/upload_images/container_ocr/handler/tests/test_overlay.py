"""Tests for the overlay pipeline in OCRProcessor._process_regional_reocr_job.

Covers:
1. Coordinate remapping (_map_bbox_to_region, _map_point_to_region, _apply_region_mapping)
2. Word matching (_y_overlap_ratio, _bbox_center_x, _match_regional_words)
3. Candidate selection (region x-filter, labeled-only preference, fallback)
4. ReceiptLine.text rebuild
5. ReceiptLetter replacement (old deleted, new created)
6. DynamoDB write ordering (add before delete)
7. is_noise recomputation
8. Integration / end-to-end (fully mocked)
"""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

# Ensure container_ocr is on sys.path so `handler` resolves directly
# without pytest walking up through infra/upload_images/__init__.py.
_container_ocr_dir = str(Path(__file__).resolve().parents[2])
if _container_ocr_dir not in sys.path:
    sys.path.insert(0, _container_ocr_dir)

import pytest
from receipt_dynamo.entities import ReceiptLetter, ReceiptLine, ReceiptWord

# Import OCRProcessor via importlib to avoid pytest's package resolution
# chain through infra/upload_images/__init__.py (which imports Pulumi).
_ocr_mod = importlib.import_module("handler.ocr_processor")
OCRProcessor = _ocr_mod.OCRProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IMG_ID = "00000000-0000-4000-8000-000000000001"
_ZERO_POINT = {"x": 0.0, "y": 0.0}
_ZERO_BBOX = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}


def _geom(x: float, y: float, w: float, h: float) -> dict:
    """Build bbox + corners for a given rectangle."""
    return {
        "bounding_box": {"x": x, "y": y, "width": w, "height": h},
        "top_left": {"x": x, "y": y},
        "top_right": {"x": x + w, "y": y},
        "bottom_left": {"x": x, "y": y + h},
        "bottom_right": {"x": x + w, "y": y + h},
    }


def _make_word(
    image_id: str = "00000000-0000-4000-8000-000000000001",
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
    text: str = "HELLO",
    x: float = 0.1,
    y: float = 0.1,
    w: float = 0.1,
    h: float = 0.05,
    confidence: float = 0.99,
    extracted_data: dict | None = None,
) -> ReceiptWord:
    g = _geom(x, y, w, h)
    return ReceiptWord(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        confidence=confidence,
        angle_degrees=0.0,
        angle_radians=0.0,
        extracted_data=extracted_data,
        **g,
    )


def _make_line(
    image_id: str = "00000000-0000-4000-8000-000000000001",
    receipt_id: int = 1,
    line_id: int = 1,
    text: str = "HELLO WORLD",
    x: float = 0.1,
    y: float = 0.1,
    w: float = 0.5,
    h: float = 0.05,
) -> ReceiptLine:
    g = _geom(x, y, w, h)
    return ReceiptLine(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        text=text,
        confidence=0.99,
        angle_degrees=0.0,
        angle_radians=0.0,
        **g,
    )


def _make_letter(
    image_id: str = "00000000-0000-4000-8000-000000000001",
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
    letter_id: int = 1,
    text: str = "H",
    x: float = 0.1,
    y: float = 0.1,
    w: float = 0.02,
    h: float = 0.05,
) -> ReceiptLetter:
    g = _geom(x, y, w, h)
    return ReceiptLetter(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        letter_id=letter_id,
        text=text,
        confidence=0.99,
        angle_degrees=0.0,
        angle_radians=0.0,
        **g,
    )


def _make_processor():
    """Build an OCRProcessor with a mocked DynamoDB client."""
    with patch("handler.ocr_processor.DynamoClient"):
        proc = OCRProcessor(
            table_name="test-table",
            raw_bucket="raw-bucket",
            site_bucket="site-bucket",
            ocr_job_queue_url="https://sqs/ocr-jobs",
            ocr_results_queue_url="https://sqs/ocr-results",
        )
    proc.dynamo = MagicMock()
    return proc


# ===========================================================================
# 1. Coordinate remapping (pure, no mocking)
# ===========================================================================

class TestMapBboxToRegion:
    def test_identity_region(self):
        """Full-image region should leave bbox unchanged."""
        proc = _make_processor()
        region = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        bbox = {"x": 0.3, "y": 0.4, "width": 0.2, "height": 0.1}
        result = proc._map_bbox_to_region(bbox, region)
        assert result == pytest.approx(bbox)

    def test_right_30pct_region(self):
        """Right-30% crop maps bbox into right slice of full image."""
        proc = _make_processor()
        region = {"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0}
        bbox = {"x": 0.0, "y": 0.5, "width": 1.0, "height": 0.1}
        result = proc._map_bbox_to_region(bbox, region)
        assert result["x"] == pytest.approx(0.70)
        assert result["y"] == pytest.approx(0.50)
        assert result["width"] == pytest.approx(0.30)
        assert result["height"] == pytest.approx(0.10)

    def test_clamping(self):
        """Coordinates are clamped to [0, 1]."""
        proc = _make_processor()
        region = {"x": 0.9, "y": 0.9, "width": 0.5, "height": 0.5}
        bbox = {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}
        result = proc._map_bbox_to_region(bbox, region)
        assert result["x"] <= 1.0
        assert result["y"] <= 1.0

    def test_narrow_region(self):
        """A narrow 5% slice should shrink width proportionally."""
        proc = _make_processor()
        region = {"x": 0.5, "y": 0.0, "width": 0.05, "height": 1.0}
        bbox = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        result = proc._map_bbox_to_region(bbox, region)
        assert result["width"] == pytest.approx(0.05)


class TestMapPointToRegion:
    def test_identity(self):
        proc = _make_processor()
        region = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        pt = {"x": 0.5, "y": 0.5}
        assert proc._map_point_to_region(pt, region) == pytest.approx(pt)

    def test_offset_region(self):
        proc = _make_processor()
        region = {"x": 0.7, "y": 0.0, "width": 0.3, "height": 1.0}
        pt = {"x": 0.0, "y": 0.5}
        result = proc._map_point_to_region(pt, region)
        assert result["x"] == pytest.approx(0.7)
        assert result["y"] == pytest.approx(0.5)

    def test_clamping_high(self):
        proc = _make_processor()
        region = {"x": 0.9, "y": 0.9, "width": 0.5, "height": 0.5}
        pt = {"x": 1.0, "y": 1.0}
        result = proc._map_point_to_region(pt, region)
        assert result["x"] == pytest.approx(1.0)
        assert result["y"] == pytest.approx(1.0)


class TestApplyRegionMapping:
    def test_mutates_all_entities(self):
        """All lines, words, and letters should have their geometry updated."""
        proc = _make_processor()
        region = {"x": 0.7, "y": 0.0, "width": 0.3, "height": 1.0}
        line = _make_line(x=0.0, y=0.5, w=1.0, h=0.05)
        word = _make_word(x=0.0, y=0.5, w=0.5, h=0.05)
        letter = _make_letter(x=0.0, y=0.5, w=0.02, h=0.05)
        proc._apply_region_mapping([line], [word], [letter], region)
        assert line.bounding_box["x"] == pytest.approx(0.7)
        assert word.bounding_box["x"] == pytest.approx(0.7)
        assert letter.bounding_box["x"] == pytest.approx(0.7)
        assert line.bounding_box["width"] == pytest.approx(0.3)
        assert word.bounding_box["width"] == pytest.approx(0.15)


# ===========================================================================
# 2. Word matching (pure)
# ===========================================================================

class TestYOverlapRatio:
    def test_perfect_overlap(self):
        proc = _make_processor()
        a = {"y": 0.1, "height": 0.1}
        b = {"y": 0.1, "height": 0.1}
        assert proc._y_overlap_ratio(a, b) == pytest.approx(1.0)

    def test_half_overlap(self):
        proc = _make_processor()
        a = {"y": 0.0, "height": 0.10}
        b = {"y": 0.05, "height": 0.10}
        assert proc._y_overlap_ratio(a, b) == pytest.approx(0.5)

    def test_zero_overlap(self):
        proc = _make_processor()
        a = {"y": 0.0, "height": 0.1}
        b = {"y": 0.5, "height": 0.1}
        assert proc._y_overlap_ratio(a, b) == pytest.approx(0.0)


class TestBboxCenterX:
    def test_basic(self):
        assert OCRProcessor._bbox_center_x({"x": 0.2, "width": 0.1}) == pytest.approx(0.25)

    def test_missing_keys(self):
        assert OCRProcessor._bbox_center_x({}) == pytest.approx(0.0)


class TestMatchRegionalWords:
    def test_one_to_one(self):
        """Each new word matches exactly one existing word."""
        proc = _make_processor()
        new_w = _make_word(text="NEW", x=0.8, y=0.1, w=0.1, h=0.05)
        old_w = _make_word(text="OLD", x=0.8, y=0.1, w=0.1, h=0.05, line_id=2, word_id=2)
        matches = proc._match_regional_words([new_w], [old_w])
        assert len(matches) == 1
        assert matches[0] == (new_w, old_w)

    def test_no_overlap_no_match(self):
        """Words with no y-overlap should not match."""
        proc = _make_processor()
        new_w = _make_word(text="NEW", x=0.8, y=0.0, w=0.1, h=0.02)
        old_w = _make_word(text="OLD", x=0.8, y=0.9, w=0.1, h=0.02, line_id=2, word_id=2)
        matches = proc._match_regional_words([new_w], [old_w])
        assert len(matches) == 0

    def test_greedy_consumption(self):
        """Once an old word is consumed, it cannot match again."""
        proc = _make_processor()
        new1 = _make_word(text="A", x=0.8, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        new2 = _make_word(text="B", x=0.8, y=0.1, w=0.1, h=0.05, line_id=1, word_id=2)
        old = _make_word(text="OLD", x=0.8, y=0.1, w=0.1, h=0.05, line_id=2, word_id=1)
        matches = proc._match_regional_words([new1, new2], [old])
        assert len(matches) == 1

    def test_x_preference(self):
        """When y-overlap is equal, closer x-center is preferred."""
        proc = _make_processor()
        new_w = _make_word(text="N", x=0.80, y=0.1, w=0.05, h=0.05, line_id=1, word_id=1)
        old_close = _make_word(text="CLOSE", x=0.80, y=0.1, w=0.05, h=0.05, line_id=2, word_id=1)
        old_far = _make_word(text="FAR", x=0.50, y=0.1, w=0.05, h=0.05, line_id=2, word_id=2)
        matches = proc._match_regional_words([new_w], [old_close, old_far])
        assert len(matches) == 1
        assert matches[0][1].text == "CLOSE"

    def test_word_splits(self):
        """Multiple new words can match multiple old words."""
        proc = _make_processor()
        new1 = _make_word(text="A", x=0.7, y=0.1, w=0.05, h=0.05, line_id=1, word_id=1)
        new2 = _make_word(text="B", x=0.85, y=0.1, w=0.05, h=0.05, line_id=1, word_id=2)
        old1 = _make_word(text="X", x=0.7, y=0.1, w=0.05, h=0.05, line_id=2, word_id=1)
        old2 = _make_word(text="Y", x=0.85, y=0.1, w=0.05, h=0.05, line_id=2, word_id=2)
        matches = proc._match_regional_words([new1, new2], [old1, old2])
        assert len(matches) == 2


# ===========================================================================
# 3. Candidate selection (mocked DynamoDB)
# ===========================================================================

class TestCandidateSelection:
    def _run_overlay(self, proc, existing_words, labels, region=None):
        """Helper to run the overlay pipeline with given state."""
        if region is None:
            region = {"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0}

        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001",
            receipt_id=1,
            reocr_region=region,
            s3_bucket="bucket",
            s3_key="key",
        )
        ocr_routing = SimpleNamespace(
            s3_bucket="bucket",
            s3_key="results/ocr.json",
            status=None,
            receipt_count=None,
            updated_at=None,
        )

        proc.dynamo.list_receipt_words_from_receipt.return_value = existing_words

        label_page = labels if labels else []
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = (
            label_page,
            None,
        )

        proc.dynamo.list_receipt_letters_from_word.return_value = []
        proc.dynamo.list_receipt_lines_from_receipt.return_value = []

        ocr_json = {
            "lines": [
                {
                    "text": "99.99",
                    "bounding_box": {"x": 0.0, "y": 0.1, "width": 1.0, "height": 0.05},
                    "top_left": {"x": 0.0, "y": 0.1},
                    "top_right": {"x": 1.0, "y": 0.1},
                    "bottom_left": {"x": 0.0, "y": 0.15},
                    "bottom_right": {"x": 1.0, "y": 0.15},
                    "confidence": 0.99,
                    "words": [
                        {
                            "text": "99.99",
                            "bounding_box": {"x": 0.0, "y": 0.1, "width": 1.0, "height": 0.05},
                            "top_left": {"x": 0.0, "y": 0.1},
                            "top_right": {"x": 1.0, "y": 0.1},
                            "bottom_left": {"x": 0.0, "y": 0.15},
                            "bottom_right": {"x": 1.0, "y": 0.15},
                            "confidence": 0.99,
                            "letters": [],
                        }
                    ],
                }
            ]
        }

        tmp = Path("/tmp/test_overlay_ocr.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch(
            "handler.ocr_processor.download_file_from_s3", return_value=tmp
        ), patch(
            "handler.ocr_processor.process_ocr_dict_as_image"
        ) as mock_parse, patch(
            "handler.ocr_processor.image_ocr_to_receipt_ocr"
        ) as mock_convert:
            # Simulate a single word from the regional OCR
            receipt_word = _make_word(
                text="99.99", x=0.0, y=0.1, w=1.0, h=0.05,
                line_id=1, word_id=1,
            )
            receipt_line = _make_line(
                text="99.99", x=0.0, y=0.1, w=1.0, h=0.05,
                line_id=1,
            )
            mock_parse.return_value = ([], [], [])
            mock_convert.return_value = ([receipt_line], [receipt_word], [])

            result = proc._process_regional_reocr_job(ocr_job, ocr_routing)
        return result

    def test_region_x_filter(self):
        """Only words whose center-x falls in the region are candidates."""
        proc = _make_processor()
        inside = _make_word(text="IN", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        outside = _make_word(text="OUT", x=0.10, y=0.1, w=0.1, h=0.05, line_id=1, word_id=2)
        self._run_overlay(proc, [inside, outside], labels=[])
        # The match should only use 'inside' since 'outside' center-x is ~0.15
        if proc.dynamo.update_receipt_words.called:
            updated = proc.dynamo.update_receipt_words.call_args[0][0]
            for w in updated:
                assert w.text != "OUT"

    def test_labeled_only_preference(self):
        """When labels exist, only labeled candidates are used."""
        proc = _make_processor()
        labeled_word = _make_word(
            text="LABELED", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1
        )
        unlabeled_word = _make_word(
            text="UNLABELED", x=0.82, y=0.1, w=0.1, h=0.05, line_id=2, word_id=1
        )
        label = SimpleNamespace(line_id=1, word_id=1)
        self._run_overlay(proc, [labeled_word, unlabeled_word], labels=[label])
        if proc.dynamo.update_receipt_words.called:
            updated = proc.dynamo.update_receipt_words.call_args[0][0]
            for w in updated:
                assert w.line_id == 1 and w.word_id == 1

    def test_fallback_no_labeled_candidates(self):
        """If labeled candidates list is empty, fall back to all region words."""
        proc = _make_processor()
        word_in_region = _make_word(
            text="W", x=0.80, y=0.1, w=0.1, h=0.05, line_id=3, word_id=1
        )
        # Label exists but for a word outside the region
        label = SimpleNamespace(line_id=99, word_id=99)
        self._run_overlay(proc, [word_in_region], labels=[label])
        # Should still produce a match because fallback kicks in
        assert proc.dynamo.update_receipt_words.called


# ===========================================================================
# 4. ReceiptLine.text rebuild
# ===========================================================================

class TestLineTextRebuild:
    def test_line_text_reconstructed(self):
        """Line text is rebuilt from updated words joined by spaces."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001",
            receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b",
            s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        existing_w1 = _make_word(text="TOTAL", x=0.30, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        existing_w2 = _make_word(text="99.98", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=2)
        existing_line = _make_line(text="TOTAL 99.98", line_id=1)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w1, existing_w2]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = []
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [existing_line]

        new_receipt_word = _make_word(text="99.99", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_receipt_line = _make_line(text="99.99", line_id=1)

        ocr_json = {"lines": [{"text": "99.99", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "words": [{"text": "99.99", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "letters": []}]}]}
        tmp = Path("/tmp/test_overlay_line.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_receipt_line], [new_receipt_word], [])):
            proc._process_regional_reocr_job(ocr_job, routing)

        assert proc.dynamo.update_receipt_lines.called
        updated_lines = proc.dynamo.update_receipt_lines.call_args[0][0]
        assert len(updated_lines) == 1
        assert updated_lines[0].text == "TOTAL 99.99"

    def test_unaffected_line_untouched(self):
        """Lines without overlaid words should not be updated."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        existing_w = _make_word(text="99.98", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        line1 = _make_line(text="99.98", line_id=1)
        line2 = _make_line(text="OTHER LINE", line_id=2, y=0.5)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = []
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [line1, line2]

        new_receipt_word = _make_word(text="99.99", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_receipt_line = _make_line(text="99.99", line_id=1)

        ocr_json = {"lines": [{"text": "99.99", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "words": [{"text": "99.99", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "letters": []}]}]}
        tmp = Path("/tmp/test_overlay_unaffected.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_receipt_line], [new_receipt_word], [])):
            proc._process_regional_reocr_job(ocr_job, routing)

        updated_lines = proc.dynamo.update_receipt_lines.call_args[0][0]
        updated_line_ids = {l.line_id for l in updated_lines}
        assert 2 not in updated_line_ids

    def test_word_ordering_by_word_id(self):
        """Words should be ordered by word_id when rebuilding text."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        # word_id=2 appears first in list but should be second in text
        w1 = _make_word(text="TOTAL", x=0.30, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        w2 = _make_word(text="OLD", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=2)
        line = _make_line(text="TOTAL OLD", line_id=1)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [w2, w1]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = []
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [line]

        new_word = _make_word(text="NEW", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text="NEW", line_id=1)

        ocr_json = {"lines": [{"text": "NEW", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "words": [{"text": "NEW", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "letters": []}]}]}
        tmp = Path("/tmp/test_overlay_order.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [])):
            proc._process_regional_reocr_job(ocr_job, routing)

        updated_lines = proc.dynamo.update_receipt_lines.call_args[0][0]
        assert updated_lines[0].text == "TOTAL NEW"


# ===========================================================================
# 5. ReceiptLetter replacement
# ===========================================================================

class TestLetterReplacement:
    def _run_with_letters(self, proc, old_letters, new_letters_from_ocr):
        """Run overlay and return (letters_to_add, letters_to_delete) from mock calls."""
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        existing_w = _make_word(text="AB", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = old_letters
        proc.dynamo.list_receipt_lines_from_receipt.return_value = []

        new_word = _make_word(text="CD", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text="CD", line_id=1)

        ocr_json = {"lines": [{"text": "CD", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "words": [{"text": "CD", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "letters": []}]}]}
        tmp = Path("/tmp/test_overlay_letters.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], new_letters_from_ocr)):
            proc._process_regional_reocr_job(ocr_job, routing)

        return proc

    def test_old_letters_deleted(self):
        proc = _make_processor()
        old_letter = _make_letter(text="A", letter_id=1)
        self._run_with_letters(proc, old_letters=[old_letter], new_letters_from_ocr=[])
        assert proc.dynamo.remove_receipt_letters.called
        deleted = proc.dynamo.remove_receipt_letters.call_args[0][0]
        assert len(deleted) == 1
        assert deleted[0].text == "A"

    def test_new_letters_use_existing_word_ids(self):
        proc = _make_processor()
        new_letter_c = _make_letter(text="C", line_id=1, word_id=1, letter_id=1, x=0.0, y=0.1, w=0.01, h=0.05)
        new_letter_d = _make_letter(text="D", line_id=1, word_id=1, letter_id=2, x=0.01, y=0.1, w=0.01, h=0.05)
        self._run_with_letters(proc, old_letters=[], new_letters_from_ocr=[new_letter_c, new_letter_d])
        assert proc.dynamo.put_receipt_letters.called
        added = proc.dynamo.put_receipt_letters.call_args[0][0]
        assert len(added) == 2
        # letters should use existing word's line_id=1, word_id=1
        for letter in added:
            assert letter.line_id == 1
            assert letter.word_id == 1

    def test_sequential_letter_ids_starting_at_1(self):
        proc = _make_processor()
        new_letter_c = _make_letter(text="C", line_id=1, word_id=1, letter_id=1, x=0.0, y=0.1, w=0.01, h=0.05)
        new_letter_d = _make_letter(text="D", line_id=1, word_id=1, letter_id=2, x=0.01, y=0.1, w=0.01, h=0.05)
        self._run_with_letters(proc, old_letters=[], new_letters_from_ocr=[new_letter_c, new_letter_d])
        added = proc.dynamo.put_receipt_letters.call_args[0][0]
        letter_ids = [l.letter_id for l in added]
        assert letter_ids == [1, 2]


# ===========================================================================
# 6. DynamoDB write ordering
# ===========================================================================

class TestWriteOrdering:
    def test_add_before_delete(self):
        """put_receipt_letters must be called before remove_receipt_letters."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        existing_w = _make_word(text="OLD", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        old_letter = _make_letter(text="O", letter_id=1)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = [old_letter]
        proc.dynamo.list_receipt_lines_from_receipt.return_value = []

        new_letter = _make_letter(text="N", line_id=1, word_id=1, letter_id=1, x=0.0, y=0.1, w=0.01, h=0.05)
        new_word = _make_word(text="NEW", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text="NEW", line_id=1)

        ocr_json = {"lines": [{"text": "NEW", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "words": [{"text": "NEW", "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "letters": []}]}]}
        tmp = Path("/tmp/test_overlay_ordering.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [new_letter])):
            proc._process_regional_reocr_job(ocr_job, routing)

        # Extract call order from the mock manager
        call_names = [c[0] for c in proc.dynamo.method_calls]
        add_idx = None
        del_idx = None
        for i, name in enumerate(call_names):
            if name == "put_receipt_letters" and add_idx is None:
                add_idx = i
            if name == "remove_receipt_letters" and del_idx is None:
                del_idx = i
        assert add_idx is not None, "put_receipt_letters was never called"
        assert del_idx is not None, "remove_receipt_letters was never called"
        assert add_idx < del_idx, "put_receipt_letters must be called before remove_receipt_letters"


# ===========================================================================
# 7. is_noise recomputation
# ===========================================================================

class TestIsNoiseRecomputation:
    def _run_overlay_with_text(self, proc, new_text):
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        existing_w = _make_word(text="OLD", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = []
        proc.dynamo.list_receipt_lines_from_receipt.return_value = []

        new_word = _make_word(text=new_text, x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text=new_text, line_id=1)

        ocr_json = {"lines": [{"text": new_text, "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "words": [{"text": new_text, "bounding_box": {"x": 0, "y": 0.1, "width": 1, "height": 0.05}, "top_left": {"x": 0, "y": 0.1}, "top_right": {"x": 1, "y": 0.1}, "bottom_left": {"x": 0, "y": 0.15}, "bottom_right": {"x": 1, "y": 0.15}, "confidence": 0.99, "letters": []}]}]}
        tmp = Path("/tmp/test_overlay_noise.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [])):
            proc._process_regional_reocr_job(ocr_job, routing)
        return proc

    def test_noise_text_detected(self):
        """A separator like '---' should be flagged as noise."""
        proc = _make_processor()
        self._run_overlay_with_text(proc, "---")
        updated = proc.dynamo.update_receipt_words.call_args[0][0]
        assert updated[0].is_noise is True

    def test_normal_text_clears_noise(self):
        """Normal text like '99.99' should not be flagged as noise."""
        proc = _make_processor()
        self._run_overlay_with_text(proc, "99.99")
        updated = proc.dynamo.update_receipt_words.call_args[0][0]
        assert updated[0].is_noise is False


# ===========================================================================
# 8. Integration / end-to-end (fully mocked)
# ===========================================================================

class TestIntegrationEndToEnd:
    def _build_ocr_json(self, words_data):
        """Build a minimal OCR JSON with the given words."""
        words = []
        for wd in words_data:
            words.append({
                "text": wd["text"],
                "bounding_box": {"x": wd.get("x", 0.0), "y": wd.get("y", 0.1), "width": wd.get("w", 1.0), "height": wd.get("h", 0.05)},
                "top_left": {"x": wd.get("x", 0.0), "y": wd.get("y", 0.1)},
                "top_right": {"x": wd.get("x", 0.0) + wd.get("w", 1.0), "y": wd.get("y", 0.1)},
                "bottom_left": {"x": wd.get("x", 0.0), "y": wd.get("y", 0.1) + wd.get("h", 0.05)},
                "bottom_right": {"x": wd.get("x", 0.0) + wd.get("w", 1.0), "y": wd.get("y", 0.1) + wd.get("h", 0.05)},
                "confidence": 0.99,
                "letters": [],
            })
        line_text = " ".join(wd["text"] for wd in words_data)
        return {
            "lines": [{
                "text": line_text,
                "bounding_box": {"x": 0.0, "y": 0.1, "width": 1.0, "height": 0.05},
                "top_left": {"x": 0.0, "y": 0.1},
                "top_right": {"x": 1.0, "y": 0.1},
                "bottom_left": {"x": 0.0, "y": 0.15},
                "bottom_right": {"x": 1.0, "y": 0.15},
                "confidence": 0.99,
                "words": words,
            }]
        }

    def test_full_flow(self):
        """End-to-end: one new word overlays one existing word, letters replaced, line rebuilt."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        existing_w = _make_word(text="OLD", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        existing_line = _make_line(text="OLD", line_id=1)
        old_letter = _make_letter(text="O", letter_id=1)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = [old_letter]
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [existing_line]

        new_word = _make_word(text="NEW", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text="NEW", line_id=1)
        new_letter_n = _make_letter(text="N", line_id=1, word_id=1, letter_id=1, x=0.0, y=0.1, w=0.01, h=0.05)

        ocr_json = self._build_ocr_json([{"text": "NEW"}])
        tmp = Path("/tmp/test_overlay_full.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [new_letter_n])):
            result = proc._process_regional_reocr_job(ocr_job, routing)

        assert result["success"] is True
        assert result["words_replaced"] == 1
        assert result["lines_rebuilt"] == 1
        assert proc.dynamo.update_receipt_words.called
        assert proc.dynamo.put_receipt_letters.called
        assert proc.dynamo.remove_receipt_letters.called
        assert proc.dynamo.update_receipt_lines.called
        assert proc.dynamo.update_ocr_routing_decision.called

    def test_no_existing_words_error(self):
        """Should return error when no existing words found."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        proc.dynamo.list_receipt_words_from_receipt.return_value = []

        ocr_json = self._build_ocr_json([{"text": "X"}])
        tmp = Path("/tmp/test_overlay_nowords.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([], [], [])):
            result = proc._process_regional_reocr_job(ocr_job, routing)

        assert result["success"] is False
        assert "No existing receipt words" in result["error"]

    def test_no_matches_still_succeeds(self):
        """If regional words don't match any existing words, overlay still succeeds with 0 replacements."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        # Existing word is far from where the regional word will land
        existing_w = _make_word(text="FAR", x=0.80, y=0.9, w=0.1, h=0.02, line_id=1, word_id=1)
        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_w]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_lines_from_receipt.return_value = []

        # New word lands at y=0.1 which has no overlap with existing at y=0.9
        new_word = _make_word(text="99.99", x=0.0, y=0.1, w=1.0, h=0.02, line_id=1, word_id=1)
        new_line = _make_line(text="99.99", line_id=1)

        ocr_json = self._build_ocr_json([{"text": "99.99", "h": 0.02}])
        tmp = Path("/tmp/test_overlay_nomatch.json")
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [])):
            result = proc._process_regional_reocr_job(ocr_job, routing)

        assert result["success"] is True
        assert result["words_replaced"] == 0

    def test_missing_receipt_id(self):
        """Should return error when receipt_id is None."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=None,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
        )
        routing = SimpleNamespace()
        result = proc._process_regional_reocr_job(ocr_job, routing)
        assert result["success"] is False
        assert "Receipt ID is None" in result["error"]

    def test_missing_region(self):
        """Should return error when reocr_region is missing/empty."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id="00000000-0000-4000-8000-000000000001", receipt_id=1,
            reocr_region=None,
        )
        routing = SimpleNamespace()
        result = proc._process_regional_reocr_job(ocr_job, routing)
        assert result["success"] is False
        assert "reocr_region is missing" in result["error"]
