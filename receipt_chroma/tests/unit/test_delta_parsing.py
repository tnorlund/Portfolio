"""Unit tests for delta parsing functions."""

import pytest

from receipt_chroma.embedding.delta.line_delta import (
    _parse_metadata_from_line_id,
)
from receipt_chroma.embedding.delta.word_delta import (
    _parse_metadata_from_custom_id,
)


@pytest.mark.unit
class TestParseLineId:
    """Test parsing line IDs."""

    def test_valid_line_id(self):
        """Test parsing valid line ID."""
        custom_id = "IMAGE#img123#RECEIPT#456#LINE#789"
        result = _parse_metadata_from_line_id(custom_id)
        assert result["image_id"] == "img123"
        assert result["receipt_id"] == 456
        assert result["line_id"] == 789
        assert result["source"] == "openai_line_embedding_batch"

    def test_invalid_line_id_wrong_parts(self):
        """Test parsing invalid line ID with wrong number of parts."""
        custom_id = "IMAGE#img123#RECEIPT#456"
        with pytest.raises(ValueError, match="Invalid custom_id format"):
            _parse_metadata_from_line_id(custom_id)

    def test_invalid_line_id_word_format(self):
        """Test parsing word ID as line ID."""
        custom_id = "IMAGE#img123#RECEIPT#456#LINE#789#WORD#123"
        with pytest.raises(ValueError, match="word embedding|8 parts"):
            _parse_metadata_from_line_id(custom_id)


@pytest.mark.unit
class TestParseWordId:
    """Test parsing word IDs."""

    def test_valid_word_id(self):
        """Test parsing valid word ID."""
        custom_id = "IMAGE#img123#RECEIPT#456#LINE#789#WORD#123"
        result = _parse_metadata_from_custom_id(custom_id)
        assert result["image_id"] == "img123"
        assert result["receipt_id"] == 456
        assert result["line_id"] == 789
        assert result["word_id"] == 123
        assert result["source"] == "openai_embedding_batch"

    def test_invalid_word_id_wrong_parts(self):
        """Test parsing invalid word ID with wrong number of parts."""
        custom_id = "IMAGE#img123#RECEIPT#456#LINE#789"
        with pytest.raises(ValueError, match="Invalid custom_id format"):
            _parse_metadata_from_custom_id(custom_id)

    def test_invalid_word_id_no_word(self):
        """Parsing word ID missing WORD component."""
        custom_id = "IMAGE#img123#RECEIPT#456#LINE#789#EXTRA#123"
        with pytest.raises(ValueError, match="line embedding"):
            _parse_metadata_from_custom_id(custom_id)


class TestStaleResultSkip:
    """A result whose primary_line_id has no visual row is skipped, not fatal."""

    def test_missing_visual_row_skips_result(self, monkeypatch):
        from unittest.mock import Mock, patch

        from receipt_chroma.embedding.delta import line_delta

        line = Mock()
        line.line_id = 1
        line.image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
        line.receipt_id = 1
        place = Mock()
        place.merchant_name = "Test Mart"
        descriptions = {
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3": {
                1: {
                    "lines": [line],
                    "words": [],
                    "labels": [],
                    "place": place,
                }
            }
        }
        results = [
            {
                # primary_line_id=99 will not exist in the grouped rows
                "custom_id": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#LINE#00099",
                "embedding": [0.1, 0.2],
            }
        ]
        captured = {}

        def fake_produce(**kwargs):
            captured.update(kwargs)
            return {
                "delta_id": "d",
                "delta_key": "k",
                "embedding_count": len(kwargs["ids"]),
            }

        with (
            patch.object(
                line_delta, "produce_embedding_delta", side_effect=fake_produce
            ),
            patch.object(
                line_delta,
                "group_lines_into_visual_rows",
                return_value=[[line]],
            ),
            patch.object(line_delta, "get_primary_line_id", return_value=1),
        ):
            out = line_delta.save_line_embeddings_as_delta(
                results=results,
                descriptions=descriptions,
                batch_id="b1",
                bucket_name="bucket",
                sqs_queue_url=None,
            )
        # all results stale: no delta produced, receipt reported for reset
        assert captured == {}  # produce_embedding_delta never called
        assert out["embedding_count"] == 0
        assert out["stale_receipts"] == [
            ["3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1]
        ]


class TestSectionWiring:
    """M1a-2: ReceiptSection rows flow into row metadata as section_label."""

    def test_section_label_lands_in_delta_metadata(self):
        from unittest.mock import Mock, patch

        from receipt_chroma.embedding.delta import line_delta

        line = Mock()
        line.line_id = 1
        line.image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
        line.receipt_id = 1
        line.text = "TOTAL 20.94"
        line.confidence = 0.99
        line.bounding_box = {"x": 0.1, "y": 0.5, "width": 0.8, "height": 0.02}
        place = Mock()
        place.merchant_name = "Test Mart"
        section = Mock(
            line_ids=[1],
            section_type="TOTAL_LINE",
            confidence=0.9,
            validation_status="PENDING",
        )
        descriptions = {
            line.image_id: {
                1: {
                    "lines": [line],
                    "words": [],
                    "labels": [],
                    "place": place,
                    "sections": [section],
                }
            }
        }
        results = [
            {
                "custom_id": f"IMAGE#{line.image_id}#RECEIPT#00001#LINE#00001",
                "embedding": [0.1, 0.2],
            }
        ]
        captured = {}

        def fake_produce(**kwargs):
            captured.update(kwargs)
            return {
                "delta_id": "d",
                "delta_key": "k",
                "embedding_count": len(kwargs["ids"]),
            }

        with (
            patch.object(
                line_delta, "produce_embedding_delta", side_effect=fake_produce
            ),
            patch.object(
                line_delta,
                "group_lines_into_visual_rows",
                return_value=[[line]],
            ),
            patch.object(line_delta, "get_primary_line_id", return_value=1),
        ):
            line_delta.save_line_embeddings_as_delta(
                results=results,
                descriptions=descriptions,
                batch_id="b1",
                bucket_name="bucket",
                sqs_queue_url=None,
            )
        assert captured["metadatas"][0].get("section_label") == "TOTAL_LINE"
