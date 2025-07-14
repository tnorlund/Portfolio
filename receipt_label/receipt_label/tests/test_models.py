import uuid
from datetime import datetime
from typing import Dict, List

import pytest
from receipt_dynamo.entities.receipt_line import (
    ReceiptLine as DynamoReceiptLine,
)
from receipt_dynamo.entities.receipt_word import (
    ReceiptWord as DynamoReceiptWord,
)

from receipt_label.models.receipt import (
    Receipt,
    ReceiptLine,
    ReceiptSection,
    ReceiptWord,
)


# Test data fixtures
@pytest.fixture
def sample_word_data() -> Dict:
    """Sample data for a ReceiptWord."""
    return {
        "text": "Sample",
        "line_id": 1,
        "word_id": 1,
        "confidence": 0.95,
        "extracted_data": {"field": "test_field"},
        "bounding_box": {"x": 0, "y": 0, "width": 100, "height": 20},
        "font_size": 12.0,
        "font_weight": "normal",
        "font_style": "normal",
    }


@pytest.fixture
def sample_line_data() -> Dict:
    """Sample data for a ReceiptLine."""
    return {
        "line_id": 1,
        "text": "Sample line text",
        "confidence": 0.95,
        "bounding_box": {"x": 0, "y": 0, "width": 200, "height": 50},
        "top_right": {"x": 200, "y": 0},
        "top_left": {"x": 0, "y": 0},
        "bottom_right": {"x": 200, "y": 50},
        "bottom_left": {"x": 0, "y": 50},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
    }


@pytest.fixture
def sample_section_data() -> Dict:
    """Sample data for a ReceiptSection."""
    return {
        "name": "header",
        "confidence": 0.9,
        "line_ids": [1],
        "spatial_patterns": ["top_aligned"],
        "content_patterns": ["date_format"],
        "start_line": 1,
        "end_line": 1,
        "metadata": {"key": "value"},
    }


@pytest.fixture
def sample_receipt_data(
    sample_word_data: Dict, sample_line_data: Dict, sample_section_data: Dict
) -> Dict:
    """Sample data for a Receipt."""
    return {
        "receipt_id": "123",
        "image_id": "test-image-id",
        "words": [sample_word_data],
        "lines": [sample_line_data],
        "sections": [sample_section_data],
        "metadata": {"store": "Test Store"},
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }


@pytest.fixture
def sample_dynamo_receipt_data() -> Dict:
    """Sample DynamoDB receipt data."""
    return {
        "receipt_id": 123,
        "image_id": str(uuid.uuid4()),
        "height": 2000,
        "width": 1000,
        "bottom_left": {"x": 0, "y": 2000},
        "bottom_right": {"x": 1000, "y": 2000},
        "top_left": {"x": 0, "y": 0},
        "top_right": {"x": 1000, "y": 0},
    }


@pytest.fixture
def sample_dynamo_word_data() -> Dict:
    """Sample DynamoDB word data."""
    return {
        "text": "Sample",
        "line_id": 1,
        "word_id": 1,
        "confidence": 0.95,
        "extracted_data": {"field": "test_field"},
        "bounding_box": {"x": 0, "y": 0, "width": 100, "height": 20},
        "top_right": {"x": 100, "y": 0},
        "top_left": {"x": 0, "y": 0},
        "bottom_right": {"x": 100, "y": 20},
        "bottom_left": {"x": 0, "y": 20},
    }


@pytest.fixture
def sample_dynamo_line_data() -> Dict:
    """Sample DynamoDB line data."""
    return {
        "line_id": 1,
        "text": "Sample line text",
        "confidence": 0.95,
        "bounding_box": {"x": 0, "y": 0, "width": 200, "height": 50},
        "top_right": {"x": 200, "y": 0},
        "top_left": {"x": 0, "y": 0},
        "bottom_right": {"x": 200, "y": 50},
        "bottom_left": {"x": 0, "y": 50},
    }


# ReceiptWord Tests
@pytest.mark.unit
class TestReceiptWord:
    def test_receipt_word_creation(self, sample_word_data: Dict):
        """Test creating a ReceiptWord instance."""
        word = ReceiptWord(**sample_word_data)
        assert word.text == "Sample"
        assert word.line_id == 1
        assert word.word_id == 1
        assert word.confidence == 0.95
        assert word.extracted_data == {"field": "test_field"}
        assert word.bounding_box == {
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 20,
        }
        assert word.font_size == 12.0
        assert word.font_weight == "normal"
        assert word.font_style == "normal"

    def test_receipt_word_from_dynamo(self):
        """Test creating a ReceiptWord from DynamoDB data."""
        image_id = str(uuid.uuid4())
        dynamo_word = DynamoReceiptWord(
            receipt_id=123,
            image_id=image_id,
            line_id=1,
            word_id=1,
            text="Sample",
            confidence=0.95,
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
            top_right={"x": 100, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 100, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            extracted_data={"field": "test_field"},
        )
        word = ReceiptWord.from_dynamo(dynamo_word)
        assert word.text == "Sample"
        assert word.line_id == 1
        assert word.word_id == 1
        assert word.confidence == 0.95
        assert word.extracted_data == {"field": "test_field"}

    def test_receipt_word_from_dynamo_dict(
        self, sample_dynamo_word_data: Dict
    ):
        """Test creating a ReceiptWord from raw DynamoDB data."""
        word = ReceiptWord(
            text=sample_dynamo_word_data["text"],
            line_id=sample_dynamo_word_data["line_id"],
            word_id=sample_dynamo_word_data["word_id"],
            confidence=sample_dynamo_word_data["confidence"],
            extracted_data=sample_dynamo_word_data.get("extracted_data"),
            bounding_box=sample_dynamo_word_data["bounding_box"],
            font_size=None,
            font_weight=None,
            font_style=None,
        )
        assert word.text == "Sample"
        assert word.line_id == 1
        assert word.word_id == 1
        assert word.confidence == 0.95
        assert word.extracted_data == {"field": "test_field"}
        assert word.bounding_box == {
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 20,
        }

    def test_receipt_word_to_dynamo_with_font_info(self):
        """Test converting ReceiptWord to DynamoReceiptWord with font information."""
        word = ReceiptWord(
            text="Sample",
            line_id=1,
            word_id=1,
            confidence=0.99,
            font_size=12.0,
            font_weight="bold",
            font_style="italic",
            extracted_data={"field": "test_field"},
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
        )
        image_id = str(uuid.uuid4())
        # Set the receipt_id and image_id attributes
        word.receipt_id = 123
        word.image_id = image_id
        dynamo_word = word.to_dynamo()
        assert dynamo_word.extracted_data["font_size"] == 12.0
        assert dynamo_word.extracted_data["font_weight"] == "bold"
        assert dynamo_word.extracted_data["font_style"] == "italic"


# ReceiptLine Tests
@pytest.mark.unit
class TestReceiptLine:
    def test_receipt_line_creation(self, sample_line_data: Dict):
        """Test creating a ReceiptLine instance."""
        line = ReceiptLine(**sample_line_data)
        assert line.text == "Sample line text"
        assert line.line_id == 1
        assert line.confidence == 0.95
        assert line.bounding_box == {
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 50,
        }
        assert line.top_right == {"x": 200, "y": 0}
        assert line.top_left == {"x": 0, "y": 0}
        assert line.bottom_right == {"x": 200, "y": 50}
        assert line.bottom_left == {"x": 0, "y": 50}

    def test_receipt_line_from_dynamo(self):
        """Test creating a ReceiptLine from DynamoDB data."""
        image_id = str(uuid.uuid4())
        dynamo_line = DynamoReceiptLine(
            receipt_id=123,
            image_id=image_id,
            line_id=1,
            text="Sample line text",
            confidence=0.95,
            bounding_box={"x": 0, "y": 0, "width": 200, "height": 50},
            top_right={"x": 200, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 200, "y": 50},
            bottom_left={"x": 0, "y": 50},
            angle_degrees=0.0,
            angle_radians=0.0,
        )
        line = ReceiptLine.from_dynamo(dynamo_line)
        assert line.text == "Sample line text"
        assert line.line_id == 1
        assert line.confidence == 0.95

    def test_receipt_line_to_dynamo(self, sample_line_data: Dict):
        """Test converting ReceiptLine to DynamoReceiptLine."""
        line = ReceiptLine(**sample_line_data)
        image_id = str(uuid.uuid4())
        # Set the receipt_id and image_id attributes
        line.receipt_id = 123
        line.image_id = image_id
        dynamo_line = line.to_dynamo()
        assert dynamo_line.receipt_id == 123
        assert dynamo_line.image_id == image_id
        assert dynamo_line.line_id == 1
        assert dynamo_line.text == "Sample line text"
        assert dynamo_line.confidence == 0.95


# ReceiptSection Tests
@pytest.mark.unit
class TestReceiptSection:
    def test_receipt_section_creation(self, sample_section_data: Dict):
        """Test creating a ReceiptSection instance."""
        section = ReceiptSection(**sample_section_data)
        assert section.name == "header"
        assert section.confidence == 0.9
        assert section.line_ids == [1]
        assert section.spatial_patterns == ["top_aligned"]
        assert section.content_patterns == ["date_format"]
        assert section.start_line == 1
        assert section.end_line == 1
        assert section.metadata == {"key": "value"}


# Receipt Tests
@pytest.mark.unit
class TestReceipt:
    def test_receipt_creation(self, sample_receipt_data: Dict):
        """Test creating a Receipt instance."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        assert receipt.receipt_id == "123"
        assert receipt.image_id == "test-image-id"
        assert len(receipt.words) == 1
        assert len(receipt.lines) == 1
        assert len(receipt.sections) == 1
        assert receipt.metadata == {"store": "Test Store"}

    def test_receipt_from_dynamo(self):
        """Test converting DynamoDB data to Receipt."""
        image_id = str(uuid.uuid4())
        dynamo_word = DynamoReceiptWord(
            receipt_id=123,
            image_id=image_id,
            line_id=1,
            word_id=1,
            text="Sample",
            confidence=0.95,
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
            top_right={"x": 100, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 100, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            extracted_data={"field": "test_field"},
        )
        dynamo_line = DynamoReceiptLine(
            receipt_id=123,
            image_id=image_id,
            line_id=1,
            text="Sample line text",
            confidence=0.95,
            bounding_box={"x": 0, "y": 0, "width": 200, "height": 50},
            top_right={"x": 200, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 200, "y": 50},
            bottom_left={"x": 0, "y": 50},
            angle_degrees=0.0,
            angle_radians=0.0,
        )
        receipt = Receipt.from_dynamo_data(
            "123", image_id, [dynamo_word], [dynamo_line]
        )
        assert receipt.receipt_id == "123"
        assert receipt.image_id == image_id
        assert len(receipt.words) == 1
        assert len(receipt.lines) == 1

    def test_receipt_from_dynamo_dict(
        self,
        sample_dynamo_receipt_data: Dict,
        sample_dynamo_word_data: Dict,
        sample_dynamo_line_data: Dict,
    ):
        """Test creating a Receipt from raw DynamoDB data (as returned by getReceiptDetails)."""
        receipt = Receipt(
            receipt_id=str(sample_dynamo_receipt_data["receipt_id"]),
            image_id=sample_dynamo_receipt_data["image_id"],
            words=[
                ReceiptWord(
                    text=sample_dynamo_word_data["text"],
                    line_id=sample_dynamo_word_data["line_id"],
                    word_id=sample_dynamo_word_data["word_id"],
                    confidence=sample_dynamo_word_data["confidence"],
                    extracted_data=sample_dynamo_word_data.get(
                        "extracted_data"
                    ),
                    bounding_box=sample_dynamo_word_data["bounding_box"],
                    font_size=None,
                    font_weight=None,
                    font_style=None,
                )
            ],
            lines=[
                ReceiptLine(
                    line_id=sample_dynamo_line_data["line_id"],
                    text=sample_dynamo_line_data["text"],
                    confidence=sample_dynamo_line_data["confidence"],
                    bounding_box=sample_dynamo_line_data["bounding_box"],
                    top_right=sample_dynamo_line_data["top_right"],
                    top_left=sample_dynamo_line_data["top_left"],
                    bottom_right=sample_dynamo_line_data["bottom_right"],
                    bottom_left=sample_dynamo_line_data["bottom_left"],
                    angle_degrees=0.0,
                    angle_radians=0.0,
                )
            ],
        )
        assert receipt.receipt_id == str(
            sample_dynamo_receipt_data["receipt_id"]
        )
        assert receipt.image_id == sample_dynamo_receipt_data["image_id"]
        assert len(receipt.words) == 1
        assert len(receipt.lines) == 1

    def test_get_words_by_line(self, sample_receipt_data: Dict):
        """Test getting words in a line."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        words = receipt.get_words_by_line(1)
        assert len(words) == 1
        assert words[0].text == "Sample"

    def test_get_line_by_id(self, sample_receipt_data: Dict):
        """Test getting a line by ID."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        line = receipt.get_line_by_id(1)
        assert line is not None
        assert line.text == "Sample line text"

    def test_get_section_by_name(self, sample_receipt_data: Dict):
        """Test getting a section by name."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        section = receipt.get_section_by_name("header")
        assert section is not None
        assert section.name == "header"

    def test_get_section_words(self, sample_receipt_data: Dict):
        """Test getting words in a section."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        section = receipt.sections[0]
        words = receipt.get_section_words(section)
        assert len(words) == 1
        assert words[0].text == "Sample"

    def test_get_section_lines(self, sample_receipt_data: Dict):
        """Test getting lines in a section."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        section = receipt.sections[0]
        lines = receipt.get_section_lines(section)
        assert len(lines) == 1
        assert lines[0].text == "Sample line text"

    def test_get_field_words(self, sample_receipt_data: Dict):
        """Test getting words by field name."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        words = receipt.get_field_words("test_field")
        assert len(words) == 1
        assert words[0].text == "Sample"

    def test_to_dict(self, sample_receipt_data: Dict):
        """Test converting Receipt to dictionary."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        receipt_dict = receipt.to_dict()
        assert receipt_dict["receipt_id"] == "123"
        assert receipt_dict["image_id"] == "test-image-id"
        assert len(receipt_dict["words"]) == 1
        assert len(receipt_dict["lines"]) == 1
        assert len(receipt_dict["sections"]) == 1

    def test_get_nonexistent_line(self, sample_receipt_data: Dict):
        """Test getting a non-existent line."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        line = receipt.get_line_by_id(999)
        assert line is None

    def test_get_nonexistent_section(self, sample_receipt_data: Dict):
        """Test getting a non-existent section."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        section = receipt.get_section_by_name("nonexistent")
        assert section is None

    def test_get_field_words_nonexistent(self, sample_receipt_data: Dict):
        """Test getting words for a field that doesn't exist."""
        receipt = Receipt(
            receipt_id=sample_receipt_data["receipt_id"],
            image_id=sample_receipt_data["image_id"],
            words=[ReceiptWord(**w) for w in sample_receipt_data["words"]],
            lines=[ReceiptLine(**l) for l in sample_receipt_data["lines"]],
            sections=[
                ReceiptSection(**s) for s in sample_receipt_data["sections"]
            ],
            metadata=sample_receipt_data["metadata"],
            created_at=sample_receipt_data["created_at"],
            updated_at=sample_receipt_data["updated_at"],
        )
        words = receipt.get_field_words("nonexistent")
        assert len(words) == 0

    def test_get_section_by_name_no_sections(self):
        """Test getting a section by name when no sections exist."""
        receipt = Receipt(
            receipt_id="123",
            image_id="test-image-id",
            words=[],
            lines=[],
            sections=None,
        )
        assert receipt.get_section_by_name("header") is None

    def test_receipt_from_dict_with_dates(self):
        """Test creating a Receipt from dictionary with datetime fields."""
        now = datetime.now()
        data = {
            "receipt_id": "123",
            "image_id": "test-image-id",
            "words": [],
            "lines": [],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        receipt = Receipt.from_dict(data)
        assert receipt.created_at == now
        assert receipt.updated_at == now
