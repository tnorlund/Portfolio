"""Shared test fixtures and sample data for receipt_upload tests."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.entities import (
    Letter,
    Line,
    OCRJob,
    OCRRoutingDecision,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
)

# Sample OCR dictionary data
SAMPLE_OCR_DICT = {
    "receipts": [
        {
            "receipt_id": 1,
            "top_left": {"x": 0.1, "y": 0.1},
            "top_right": {"x": 0.9, "y": 0.1},
            "bottom_left": {"x": 0.1, "y": 0.9},
            "bottom_right": {"x": 0.9, "y": 0.9},
        }
    ],
    "lines": [
        {
            "line_id": 1,
            "receipt_id": 1,
            "text": "STORE NAME",
            "confidence": 0.95,
            "top_left": {"x": 0.2, "y": 0.2},
            "top_right": {"x": 0.8, "y": 0.2},
            "bottom_left": {"x": 0.2, "y": 0.25},
            "bottom_right": {"x": 0.8, "y": 0.25},
        },
        {
            "line_id": 2,
            "receipt_id": 1,
            "text": "123 Main St",
            "confidence": 0.92,
            "top_left": {"x": 0.2, "y": 0.3},
            "top_right": {"x": 0.8, "y": 0.3},
            "bottom_left": {"x": 0.2, "y": 0.35},
            "bottom_right": {"x": 0.8, "y": 0.35},
        },
    ],
    "words": [
        {
            "word_id": 1,
            "line_id": 1,
            "receipt_id": 1,
            "text": "STORE",
            "confidence": 0.96,
            "top_left": {"x": 0.2, "y": 0.2},
            "top_right": {"x": 0.45, "y": 0.2},
            "bottom_left": {"x": 0.2, "y": 0.25},
            "bottom_right": {"x": 0.45, "y": 0.25},
        },
        {
            "word_id": 2,
            "line_id": 1,
            "receipt_id": 1,
            "text": "NAME",
            "confidence": 0.94,
            "top_left": {"x": 0.55, "y": 0.2},
            "top_right": {"x": 0.8, "y": 0.2},
            "bottom_left": {"x": 0.55, "y": 0.25},
            "bottom_right": {"x": 0.8, "y": 0.25},
        },
    ],
    "letters": [
        {
            "letter_id": 1,
            "word_id": 1,
            "line_id": 1,
            "receipt_id": 1,
            "text": "S",
            "confidence": 0.97,
            "top_left": {"x": 0.2, "y": 0.2},
            "top_right": {"x": 0.25, "y": 0.2},
            "bottom_left": {"x": 0.2, "y": 0.25},
            "bottom_right": {"x": 0.25, "y": 0.25},
        }
    ],
}


# Sample Apple Vision OCR result
SAMPLE_APPLE_OCR_RESULT = {
    "version": "1.0",
    "image_width": 1024,
    "image_height": 768,
    "text_blocks": [
        {
            "text": "STORE NAME\n123 Main St",
            "confidence": 0.95,
            "bounding_box": {"x": 100, "y": 100, "width": 800, "height": 200},
        }
    ],
}


# Sample receipt image metadata
SAMPLE_IMAGE_METADATA = {
    "image_id": "test-image-123",
    "s3_bucket": "test-receipt-bucket",
    "s3_key": "receipts/2024/01/test-image-123.jpg",
    "upload_timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    "file_size": 2048576,  # 2MB
    "image_format": "JPEG",
    "dimensions": {"width": 1024, "height": 768},
}


class SampleEntityFactory:
    """Factory for creating sample entity objects for testing."""

    @staticmethod
    def create_line(
        line_id: int = 1,
        receipt_id: int = 1,
        text: str = "Sample Line",
        confidence: float = 0.95,
        **kwargs,
    ) -> Line:
        """Create a sample Line entity."""
        return Line(
            line_id=line_id,
            receipt_id=receipt_id,
            text=text,
            confidence=confidence,
            top_left=kwargs.get("top_left", {"x": 0.1, "y": 0.1}),
            top_right=kwargs.get("top_right", {"x": 0.9, "y": 0.1}),
            bottom_left=kwargs.get("bottom_left", {"x": 0.1, "y": 0.2}),
            bottom_right=kwargs.get("bottom_right", {"x": 0.9, "y": 0.2}),
            angle_degrees=kwargs.get("angle_degrees", 0.0),
        )

    @staticmethod
    def create_word(
        word_id: int = 1,
        line_id: int = 1,
        receipt_id: int = 1,
        text: str = "Sample",
        confidence: float = 0.96,
        **kwargs,
    ) -> Word:
        """Create a sample Word entity."""
        return Word(
            word_id=word_id,
            line_id=line_id,
            receipt_id=receipt_id,
            text=text,
            confidence=confidence,
            top_left=kwargs.get("top_left", {"x": 0.1, "y": 0.1}),
            top_right=kwargs.get("top_right", {"x": 0.3, "y": 0.1}),
            bottom_left=kwargs.get("bottom_left", {"x": 0.1, "y": 0.15}),
            bottom_right=kwargs.get("bottom_right", {"x": 0.3, "y": 0.15}),
        )

    @staticmethod
    def create_letter(
        letter_id: int = 1,
        word_id: int = 1,
        line_id: int = 1,
        receipt_id: int = 1,
        text: str = "S",
        confidence: float = 0.97,
        **kwargs,
    ) -> Letter:
        """Create a sample Letter entity."""
        return Letter(
            letter_id=letter_id,
            word_id=word_id,
            line_id=line_id,
            receipt_id=receipt_id,
            text=text,
            confidence=confidence,
            top_left=kwargs.get("top_left", {"x": 0.1, "y": 0.1}),
            top_right=kwargs.get("top_right", {"x": 0.12, "y": 0.1}),
            bottom_left=kwargs.get("bottom_left", {"x": 0.1, "y": 0.12}),
            bottom_right=kwargs.get("bottom_right", {"x": 0.12, "y": 0.12}),
        )

    @staticmethod
    def create_receipt_line(
        image_id: str = "test-image-123",
        receipt_id: int = 1,
        line_id: int = 1,
        text: str = "Receipt Line",
        **kwargs,
    ) -> ReceiptLine:
        """Create a sample ReceiptLine entity."""
        return ReceiptLine(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            text=text,
            confidence=kwargs.get("confidence", 0.95),
            top_left=kwargs.get("top_left", {"x": 0.1, "y": 0.1}),
            top_right=kwargs.get("top_right", {"x": 0.9, "y": 0.1}),
            bottom_left=kwargs.get("bottom_left", {"x": 0.1, "y": 0.2}),
            bottom_right=kwargs.get("bottom_right", {"x": 0.9, "y": 0.2}),
            angle_degrees=kwargs.get("angle_degrees", 0.0),
        )

    @staticmethod
    def create_ocr_job(
        job_id: str = "job-123",
        image_id: str = "test-image-123",
        status: str = OCRStatus.PENDING.value,
        **kwargs,
    ) -> OCRJob:
        """Create a sample OCRJob entity."""
        now = datetime.now(timezone.utc)
        return OCRJob(
            job_id=job_id,
            image_id=image_id,
            status=status,
            s3_bucket=kwargs.get("s3_bucket", "test-bucket"),
            s3_key=kwargs.get("s3_key", f"images/{image_id}.jpg"),
            created_at=kwargs.get("created_at", now),
            updated_at=kwargs.get("updated_at", now),
            image_count=kwargs.get("image_count", 1),
            error_message=kwargs.get("error_message"),
        )

    @staticmethod
    def create_ocr_routing_decision(
        image_id: str = "test-image-123",
        job_id: str = "job-123",
        status: str = OCRStatus.PENDING.value,
        **kwargs,
    ) -> OCRRoutingDecision:
        """Create a sample OCRRoutingDecision entity."""
        now = datetime.now(timezone.utc)
        return OCRRoutingDecision(
            image_id=image_id,
            job_id=job_id,
            status=status,
            s3_bucket=kwargs.get("s3_bucket", "test-bucket"),
            s3_key=kwargs.get("s3_key", f"ocr_results/{image_id}.json"),
            created_at=kwargs.get("created_at", now),
            updated_at=kwargs.get("updated_at", now),
            receipt_count=kwargs.get("receipt_count", 0),
        )


class GeometryTestData:
    """Test data for geometry operations."""

    @staticmethod
    def create_receipt_lines(count: int = 5) -> List[Dict[str, Any]]:
        """Create sample receipt lines for geometry testing."""
        lines = []
        for i in range(count):
            y_top = 0.1 + i * 0.15
            y_bottom = y_top + 0.05
            lines.append(
                {
                    "line_id": i + 1,
                    "receipt_id": 1,
                    "text": f"Line {i + 1}",
                    "confidence": 0.90 + i * 0.01,
                    "top_left": {"x": 0.1, "y": y_top},
                    "top_right": {"x": 0.9, "y": y_top},
                    "bottom_left": {"x": 0.1, "y": y_bottom},
                    "bottom_right": {"x": 0.9, "y": y_bottom},
                    "angle_degrees": 0.0,
                }
            )
        return lines

    @staticmethod
    def create_skewed_lines(angle_degrees: float = 15) -> List[Dict[str, Any]]:
        """Create sample lines with rotation/skew."""
        import math

        lines = []
        for i in range(3):
            y_base = 0.2 + i * 0.2
            x_offset = math.tan(math.radians(angle_degrees)) * 0.1
            lines.append(
                {
                    "line_id": i + 1,
                    "receipt_id": 1,
                    "text": f"Skewed Line {i + 1}",
                    "confidence": 0.93,
                    "top_left": {"x": 0.1 + x_offset * i, "y": y_base},
                    "top_right": {"x": 0.9 + x_offset * i, "y": y_base},
                    "bottom_left": {
                        "x": 0.1 + x_offset * i,
                        "y": y_base + 0.05,
                    },
                    "bottom_right": {
                        "x": 0.9 + x_offset * i,
                        "y": y_base + 0.05,
                    },
                    "angle_degrees": angle_degrees,
                }
            )
        return lines

    @staticmethod
    def create_clustered_lines() -> List[Dict[str, Any]]:
        """Create lines that should cluster together."""
        clusters = []
        # Cluster 1 - header lines
        for i in range(3):
            clusters.append(
                {
                    "line_id": i + 1,
                    "receipt_id": 1,
                    "text": f"Header {i + 1}",
                    "confidence": 0.95,
                    "top_left": {"x": 0.1, "y": 0.1 + i * 0.03},
                    "top_right": {"x": 0.9, "y": 0.1 + i * 0.03},
                    "bottom_left": {"x": 0.1, "y": 0.13 + i * 0.03},
                    "bottom_right": {"x": 0.9, "y": 0.13 + i * 0.03},
                    "angle_degrees": 0.0,
                }
            )
        # Cluster 2 - item lines
        for i in range(4):
            clusters.append(
                {
                    "line_id": i + 4,
                    "receipt_id": 1,
                    "text": f"Item {i + 1}",
                    "confidence": 0.92,
                    "top_left": {"x": 0.1, "y": 0.4 + i * 0.04},
                    "top_right": {"x": 0.9, "y": 0.4 + i * 0.04},
                    "bottom_left": {"x": 0.1, "y": 0.44 + i * 0.04},
                    "bottom_right": {"x": 0.9, "y": 0.44 + i * 0.04},
                    "angle_degrees": 0.0,
                }
            )
        return clusters


# Pytest fixtures
@pytest.fixture
def sample_ocr_dict():
    """Provide sample OCR dictionary data."""
    return SAMPLE_OCR_DICT.copy()


@pytest.fixture
def sample_apple_ocr_result():
    """Provide sample Apple Vision OCR result."""
    return SAMPLE_APPLE_OCR_RESULT.copy()


@pytest.fixture
def sample_image_metadata():
    """Provide sample image metadata."""
    return SAMPLE_IMAGE_METADATA.copy()


@pytest.fixture
def entity_factory():
    """Provide entity factory for creating test objects."""
    return SampleEntityFactory()


@pytest.fixture
def geometry_test_data():
    """Provide geometry test data helper."""
    return GeometryTestData()


@pytest.fixture
def sample_lines():
    """Create a set of sample Line entities."""
    factory = SampleEntityFactory()
    return [factory.create_line(line_id=i, text=f"Line {i}") for i in range(1, 6)]


@pytest.fixture
def sample_words():
    """Create a set of sample Word entities."""
    factory = SampleEntityFactory()
    words = []
    for line_id in range(1, 4):
        for word_num in range(1, 4):
            word_id = (line_id - 1) * 3 + word_num
            words.append(
                factory.create_word(
                    word_id=word_id, line_id=line_id, text=f"Word{word_num}"
                )
            )
    return words


@pytest.fixture
def sample_receipt_entities():
    """Create a complete set of receipt entities."""
    factory = SampleEntityFactory()
    image_id = "test-receipt-001"

    lines = [
        factory.create_receipt_line(
            image_id=image_id, line_id=i, text=f"Receipt Line {i}"
        )
        for i in range(1, 4)
    ]

    words = []
    letters = []

    for line_idx, line in enumerate(lines):
        # Create 2-3 words per line
        for word_num in range(2 + line_idx % 2):
            word_id = line.line_id * 10 + word_num
            word = ReceiptWord(
                image_id=image_id,
                receipt_id=line.receipt_id,
                line_id=line.line_id,
                word_id=word_id,
                text=f"Word{word_num}",
                confidence=0.94,
                top_left={
                    "x": 0.1 + word_num * 0.3,
                    "y": 0.1 + line_idx * 0.2,
                },
                top_right={
                    "x": 0.3 + word_num * 0.3,
                    "y": 0.1 + line_idx * 0.2,
                },
                bottom_left={
                    "x": 0.1 + word_num * 0.3,
                    "y": 0.15 + line_idx * 0.2,
                },
                bottom_right={
                    "x": 0.3 + word_num * 0.3,
                    "y": 0.15 + line_idx * 0.2,
                },
            )
            words.append(word)

            # Create letters for each word
            for letter_idx, char in enumerate(word.text):
                letter = ReceiptLetter(
                    image_id=image_id,
                    receipt_id=line.receipt_id,
                    line_id=line.line_id,
                    word_id=word.word_id,
                    letter_id=word.word_id * 10 + letter_idx,
                    text=char,
                    confidence=0.96,
                    top_left={"x": 0.1, "y": 0.1},
                    top_right={"x": 0.12, "y": 0.1},
                    bottom_left={"x": 0.1, "y": 0.12},
                    bottom_right={"x": 0.12, "y": 0.12},
                )
                letters.append(letter)

    return {"lines": lines, "words": words, "letters": letters}


@pytest.fixture
def mock_s3_config():
    """Provide mock S3 configuration."""
    return {
        "bucket": "test-receipt-bucket",
        "prefix": "receipts/2024/01/",
        "region": "us-east-1",
    }


@pytest.fixture
def mock_dynamo_config():
    """Provide mock DynamoDB configuration."""
    return {
        "table_name": "test-receipt-table",
        "region": "us-east-1",
        "read_capacity": 5,
        "write_capacity": 5,
    }
