"""Tests for the overlay pipeline in OCRProcessor._process_regional_reocr_job.

Covers:
1. Coordinate remapping (_map_bbox_to_region, _map_point_to_region, _apply_region_mapping)
2. Word matching (_y_overlap_ratio, _bbox_center_x, _match_regional_words)
3. Candidate selection (region x-filter, y-filter)
4. ReceiptLine.text rebuild
5. ReceiptLetter replacement (old deleted, new created)
6. DynamoDB write ordering (add before delete)
7. is_noise recomputation
8. Integration / end-to-end (fully mocked)
"""

import importlib
import json
import sys
import tempfile
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
    # Default receipt corners: unit square (identity transform).
    # Receipt-relative coords == full-image Vision coords.
    # width/height must be set for the perspective coefficient computation.
    proc.dynamo.get_receipt.return_value = SimpleNamespace(
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_left={"x": 0.0, "y": 0.0},
        bottom_right={"x": 1.0, "y": 0.0},
        width=1000,
        height=2000,
    )
    proc.dynamo.get_image.return_value = SimpleNamespace(
        width=1000,
        height=2000,
    )
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

        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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

    def test_region_y_filter(self):
        """Only words whose center-y falls in the region are candidates."""
        proc = _make_processor()
        # Region y=0.0, height=1.0 — default covers full height.
        # Use a narrow region to test y-filtering.
        region = {"x": 0.70, "y": 0.05, "width": 0.30, "height": 0.15}
        inside = _make_word(
            text="IN", x=0.80, y=0.08, w=0.1, h=0.05, line_id=1, word_id=1
        )
        outside = _make_word(
            text="OUT", x=0.80, y=0.50, w=0.1, h=0.05, line_id=2, word_id=1
        )
        self._run_overlay(proc, [inside, outside], labels=[], region=region)
        if proc.dynamo.update_receipt_words.called:
            updated = proc.dynamo.update_receipt_words.call_args[0][0]
            for w in updated:
                assert w.text != "OUT"

    def test_unlabeled_words_are_candidates(self):
        """Both labeled and unlabeled words in the region are candidates."""
        proc = _make_processor()
        unlabeled_word = _make_word(
            text="W", x=0.80, y=0.1, w=0.1, h=0.05, line_id=3, word_id=1
        )
        # Label exists but for a word outside the region
        label = SimpleNamespace(line_id=99, word_id=99)
        self._run_overlay(proc, [unlabeled_word], labels=[label])
        # Unlabeled word should still be a candidate
        assert proc.dynamo.update_receipt_words.called


# ===========================================================================
# 3b. Unmatched new word addition
# ===========================================================================


class TestUnmatchedWordAddition:
    """When re-OCR finds words that don't match any existing candidate,
    they should be added as new ReceiptWords on the best-matching line."""

    def _run_overlay(self, proc, existing_words, new_words, new_lines=None,
                     region=None, labels=None, new_letters=None):
        """Run the overlay pipeline with explicit new words."""
        if region is None:
            region = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        if new_lines is None:
            new_lines = []
        if new_letters is None:
            new_letters = []

        ocr_job = SimpleNamespace(
            image_id=_IMG_ID, receipt_id=1,
            reocr_region=region, s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        proc.dynamo.list_receipt_words_from_receipt.return_value = existing_words
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = (
            [SimpleNamespace(line_id=l.line_id, word_id=l.word_id) for l in (labels or [])],
            None,
        )
        proc.dynamo.list_receipt_letters_from_word.return_value = []
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [
            _make_line(line_id=lid, text="placeholder")
            for lid in {w.line_id for w in existing_words}
        ]

        ocr_json = {"lines": []}
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr",
                   return_value=(new_lines, new_words, new_letters)):
            result = proc._process_regional_reocr_job(ocr_job, routing)
        return result

    def test_unmatched_word_added(self):
        """A new OCR word with no remaining candidate gets added."""
        proc = _make_processor()
        # One existing word on line 1
        existing = _make_word(text="CARLON", x=0.1, y=0.5, w=0.15, h=0.05,
                              line_id=1, word_id=1)
        # Two new OCR words: first one matches existing, second has no match
        new_match = _make_word(text="CARLON", x=0.1, y=0.5, w=0.15, h=0.05,
                               line_id=1, word_id=1)
        new_extra = _make_word(text="8.68", x=0.85, y=0.5, w=0.1, h=0.05,
                               line_id=1, word_id=2)

        result = self._run_overlay(proc, [existing], [new_match, new_extra])

        assert result["words_added"] == 1
        assert proc.dynamo.add_receipt_words.called
        added = proc.dynamo.add_receipt_words.call_args[0][0]
        assert len(added) == 1
        assert added[0].text == "8.68"
        assert added[0].line_id == 1
        assert added[0].word_id == 2  # existing max=1, so next=2

    def test_unmatched_word_no_overlap_skipped(self):
        """A new word with no Y-overlap to any line is skipped."""
        proc = _make_processor()
        existing = _make_word(text="A", x=0.1, y=0.1, w=0.1, h=0.05,
                              line_id=1, word_id=1)
        # New word at completely different Y
        new_word = _make_word(text="X", x=0.1, y=0.9, w=0.1, h=0.05,
                              line_id=1, word_id=1)

        result = self._run_overlay(proc, [existing], [new_word])

        assert result.get("words_added", 0) == 0

    def test_line_text_includes_added_words(self):
        """ReceiptLine text is rebuilt to include the added word."""
        proc = _make_processor()
        existing = _make_word(text="ADJUSTABLE", x=0.1, y=0.5, w=0.2, h=0.05,
                              line_id=1, word_id=1)
        new_word = _make_word(text="8.68", x=0.85, y=0.5, w=0.1, h=0.05,
                              line_id=1, word_id=1)

        self._run_overlay(proc, [existing], [new_word])

        assert proc.dynamo.update_receipt_lines.called
        updated_lines = proc.dynamo.update_receipt_lines.call_args[0][0]
        assert any("8.68" in line.text for line in updated_lines)


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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
    def test_delete_before_add(self):
        """remove_receipt_letters must be called before put_receipt_letters.

        Old and new letters can share the same (line_id, word_id, letter_id)
        keys, so adding first then deleting would clobber the new data.
        """
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        assert del_idx < add_idx, "remove_receipt_letters must be called before put_receipt_letters"


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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
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


# ===========================================================================
# 9. Orphan deletion
# ===========================================================================

class TestOrphanDeletion:
    """Tests for deleting candidate words that aren't matched by new re-OCR words."""

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

    def test_orphaned_word_deleted(self):
        """An existing word in the re-OCR region that no new word matches should be deleted."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id=_IMG_ID, receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        # Two existing words in the region: "GOOD" at x=0.75, "JUNK" at x=0.90.
        # Re-OCR produces only one word ("GOOD") matching the first.
        # "JUNK" becomes an orphan and should be deleted.
        existing_good = _make_word(text="GOOD", x=0.75, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        existing_junk = _make_word(text="JUNK", x=0.90, y=0.1, w=0.05, h=0.05, line_id=1, word_id=2)
        junk_letter = _make_letter(text="J", line_id=1, word_id=2, letter_id=1, x=0.90, y=0.1, w=0.01, h=0.05)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_good, existing_junk]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)

        # list_receipt_letters_from_word is called for matched words AND orphans.
        # First call: matched word (existing_good) — returns empty.
        # Second call: orphan (existing_junk) — returns its letter.
        proc.dynamo.list_receipt_letters_from_word.side_effect = [[], [junk_letter]]

        existing_line = _make_line(text="GOOD JUNK", line_id=1)
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [existing_line]

        # Re-OCR produces one word "BETTER" that matches "GOOD" by position.
        new_word = _make_word(text="BETTER", x=0.0, y=0.1, w=0.5, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text="BETTER", line_id=1)

        ocr_json = self._build_ocr_json([{"text": "BETTER", "x": 0.0, "w": 0.5}])
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [])):
            result = proc._process_regional_reocr_job(ocr_job, routing)

        assert result["success"] is True
        assert result["words_deleted"] == 1
        assert result["words_replaced"] == 1

        # Verify delete_receipt_words was called with the orphan.
        proc.dynamo.delete_receipt_words.assert_called_once()
        deleted_words = proc.dynamo.delete_receipt_words.call_args[0][0]
        assert len(deleted_words) == 1
        assert deleted_words[0].text == "JUNK"

        # Verify orphan's letter was included in remove_receipt_letters.
        proc.dynamo.remove_receipt_letters.assert_called_once()
        deleted_letters = proc.dynamo.remove_receipt_letters.call_args[0][0]
        assert any(l.text == "J" for l in deleted_letters)

    def test_orphan_excluded_from_line_text_rebuild(self):
        """Rebuilt ReceiptLine.text must not contain orphaned words."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id=_IMG_ID, receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        # Three existing words on line 1, all inside the region.
        # Re-OCR matches word 1 ("AAA") and word 3 ("CCC") but NOT word 2 ("GARBLED").
        existing_a = _make_word(text="AAA", x=0.72, y=0.1, w=0.05, h=0.05, line_id=1, word_id=1)
        existing_garble = _make_word(text="GARBLED", x=0.80, y=0.1, w=0.05, h=0.05, line_id=1, word_id=2)
        existing_c = _make_word(text="CCC", x=0.88, y=0.1, w=0.05, h=0.05, line_id=1, word_id=3)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_a, existing_garble, existing_c]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)

        # Letters: matched words return empty, orphan returns its letters.
        garble_letters = [
            _make_letter(text="G", line_id=1, word_id=2, letter_id=1, x=0.80, y=0.1),
            _make_letter(text="A", line_id=1, word_id=2, letter_id=2, x=0.81, y=0.1),
        ]
        # Calls: word_id=1 match, word_id=3 match, then orphan word_id=2.
        proc.dynamo.list_receipt_letters_from_word.side_effect = [[], [], garble_letters]

        existing_line = _make_line(text="AAA GARBLED CCC", line_id=1)
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [existing_line]

        # Re-OCR produces 2 words at the same Y, matching word 1 and word 3.
        new_a = _make_word(text="ALPHA", x=0.0, y=0.1, w=0.3, h=0.05, line_id=1, word_id=1)
        new_c = _make_word(text="GAMMA", x=0.5, y=0.1, w=0.3, h=0.05, line_id=1, word_id=2)
        new_line = _make_line(text="ALPHA GAMMA", line_id=1)

        ocr_json = self._build_ocr_json([
            {"text": "ALPHA", "x": 0.0, "w": 0.3},
            {"text": "GAMMA", "x": 0.5, "w": 0.3},
        ])
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_a, new_c], [])):
            result = proc._process_regional_reocr_job(ocr_job, routing)

        assert result["success"] is True
        assert result["words_deleted"] == 1

        # The rebuilt line text must contain the updated words and exclude the orphan.
        proc.dynamo.update_receipt_lines.assert_called_once()
        rebuilt_lines = proc.dynamo.update_receipt_lines.call_args[0][0]
        rebuilt_text = rebuilt_lines[0].text
        assert "GARBLED" not in rebuilt_text
        assert "ALPHA" in rebuilt_text
        assert "GAMMA" in rebuilt_text

    def test_orphan_letters_deleted_before_words(self):
        """Orphan letters must be removed before orphan words are deleted."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id=_IMG_ID, receipt_id=1,
            reocr_region={"x": 0.70, "y": 0.0, "width": 0.30, "height": 1.0},
            s3_bucket="b", s3_key="k",
        )
        routing = SimpleNamespace(
            s3_bucket="b", s3_key="r.json",
            status=None, receipt_count=None, updated_at=None,
        )

        # One existing word in the region, no re-OCR words match it.
        existing_orphan = _make_word(text="STALE", x=0.80, y=0.1, w=0.1, h=0.05, line_id=1, word_id=1)
        orphan_letter = _make_letter(text="S", line_id=1, word_id=1, letter_id=1, x=0.80, y=0.1)

        # Also add an existing word OUTSIDE the region so the receipt has words.
        existing_outside = _make_word(text="KEEPER", x=0.10, y=0.1, w=0.1, h=0.05, line_id=1, word_id=2)

        proc.dynamo.list_receipt_words_from_receipt.return_value = [existing_orphan, existing_outside]
        proc.dynamo.list_receipt_word_labels_for_receipt.return_value = ([], None)
        proc.dynamo.list_receipt_letters_from_word.return_value = [orphan_letter]
        proc.dynamo.list_receipt_lines_from_receipt.return_value = [
            _make_line(text="STALE KEEPER", line_id=1),
        ]

        # Re-OCR produces no words (region was all noise).
        ocr_json = self._build_ocr_json([{"text": "X", "x": 0.0, "y": 0.8, "h": 0.01}])
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(ocr_json))

        new_word = _make_word(text="X", x=0.0, y=0.8, w=0.01, h=0.01, line_id=1, word_id=1)
        new_line = _make_line(text="X", line_id=1)

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [])):
            proc._process_regional_reocr_job(ocr_job, routing)

        # Extract call order: remove_receipt_letters must precede delete_receipt_words.
        call_names = [c[0] for c in proc.dynamo.method_calls]
        remove_idx = next(i for i, n in enumerate(call_names) if n == "remove_receipt_letters")
        delete_idx = next(i for i, n in enumerate(call_names) if n == "delete_receipt_words")
        assert remove_idx < delete_idx, (
            "remove_receipt_letters must be called before delete_receipt_words"
        )

    def test_no_orphans_when_all_matched(self):
        """When every candidate word matches a re-OCR word, nothing should be deleted."""
        proc = _make_processor()
        ocr_job = SimpleNamespace(
            image_id=_IMG_ID, receipt_id=1,
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

        new_word = _make_word(text="NEW", x=0.0, y=0.1, w=1.0, h=0.05, line_id=1, word_id=1)
        new_line = _make_line(text="NEW", line_id=1)

        ocr_json = self._build_ocr_json([{"text": "NEW"}])
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(ocr_json))

        with patch("handler.ocr_processor.download_file_from_s3", return_value=tmp), \
             patch("handler.ocr_processor.process_ocr_dict_as_image", return_value=([], [], [])), \
             patch("handler.ocr_processor.image_ocr_to_receipt_ocr", return_value=([new_line], [new_word], [])):
            result = proc._process_regional_reocr_job(ocr_job, routing)

        assert result["success"] is True
        assert result["words_deleted"] == 0
        proc.dynamo.delete_receipt_words.assert_not_called()
