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
