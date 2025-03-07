from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
from receipt_dynamo.entities.receipt_word import ReceiptWord as DynamoReceiptWord


@dataclass
class ReceiptWord:
    """Represents a single word in a receipt."""

    text: str
    line_id: int
    word_id: int
    confidence: float
    extracted_data: Optional[Dict] = None
    bounding_box: Optional[Dict] = None
    font_size: Optional[float] = None
    font_weight: Optional[str] = None
    font_style: Optional[str] = None

    @classmethod
    def from_dynamo(cls, word: DynamoReceiptWord) -> "ReceiptWord":
        """Convert a DynamoDB ReceiptWord to a ReceiptWord for labeling.

        Args:
            word: DynamoDB ReceiptWord instance

        Returns:
            ReceiptWord instance for labeling
        """
        return cls(
            text=word.text,
            line_id=word.line_id,
            word_id=word.word_id,
            confidence=word.confidence,
            extracted_data=word.extracted_data,
            bounding_box=word.bounding_box,
            # Extract font information from extracted_data if available
            font_size=(
                word.extracted_data.get("font_size") if word.extracted_data else None
            ),
            font_weight=(
                word.extracted_data.get("font_weight") if word.extracted_data else None
            ),
            font_style=(
                word.extracted_data.get("font_style") if word.extracted_data else None
            ),
        )

    def to_dynamo(self, receipt_id: int, image_id: str) -> DynamoReceiptWord:
        """Convert this ReceiptWord back to a DynamoDB ReceiptWord.

        Args:
            receipt_id: The receipt ID
            image_id: The image ID

        Returns:
            DynamoDB ReceiptWord instance
        """
        # Update extracted_data with font information
        extracted_data = self.extracted_data or {}
        if self.font_size:
            extracted_data["font_size"] = self.font_size
        if self.font_weight:
            extracted_data["font_weight"] = self.font_weight
        if self.font_style:
            extracted_data["font_style"] = self.font_style

        return DynamoReceiptWord(
            receipt_id=receipt_id,
            image_id=image_id,
            line_id=self.line_id,
            word_id=self.word_id,
            text=self.text,
            bounding_box=self.bounding_box or {},
            top_right={"x": 0, "y": 0},  # These will be calculated by DynamoDB
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 0, "y": 0},
            bottom_left={"x": 0, "y": 0},
            angle_degrees=0.0,  # These will be calculated by DynamoDB
            angle_radians=0.0,
            confidence=self.confidence,
            extracted_data=extracted_data,
        )


@dataclass
class ReceiptSection:
    """Represents a section in a receipt."""

    name: str
    confidence: float
    line_ids: List[int]
    spatial_patterns: List[str]
    content_patterns: List[str]
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    metadata: Optional[Dict] = None


@dataclass
class Receipt:
    """Represents a complete receipt."""

    receipt_id: str
    image_id: str
    words: List[ReceiptWord]
    sections: Optional[List[ReceiptSection]] = None
    metadata: Optional[Dict] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_dynamo(
        cls, receipt_id: str, image_id: str, words: List[DynamoReceiptWord]
    ) -> "Receipt":
        """Create a Receipt instance from DynamoDB data.

        Args:
            receipt_id: The receipt ID
            image_id: The image ID
            words: List of DynamoDB ReceiptWord instances

        Returns:
            Receipt instance for labeling
        """
        return cls(
            receipt_id=receipt_id,
            image_id=image_id,
            words=[ReceiptWord.from_dynamo(word) for word in words],
            metadata=None,
            created_at=None,
            updated_at=None,
        )

    def get_words_by_line(self, line_id: int) -> List[ReceiptWord]:
        """Get all words in a specific line."""
        return [word for word in self.words if word.line_id == line_id]

    def get_section_by_name(self, name: str) -> Optional[ReceiptSection]:
        """Get a section by its name."""
        if not self.sections:
            return None
        for section in self.sections:
            if section.name == name:
                return section
        return None

    def get_section_words(self, section: ReceiptSection) -> List[ReceiptWord]:
        """Get all words in a specific section."""
        return [word for word in self.words if word.line_id in section.line_ids]

    def get_field_words(self, field_name: str) -> List[ReceiptWord]:
        """Get all words associated with a specific field."""
        return [
            word
            for word in self.words
            if word.extracted_data and word.extracted_data.get("field") == field_name
        ]

    def to_dict(self) -> Dict:
        """Convert receipt to dictionary format."""
        return {
            "receipt_id": self.receipt_id,
            "image_id": self.image_id,
            "words": [
                {
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "confidence": word.confidence,
                    "extracted_data": word.extracted_data,
                    "bounding_box": word.bounding_box,
                    "font_size": word.font_size,
                    "font_weight": word.font_weight,
                    "font_style": word.font_style,
                }
                for word in self.words
            ],
            "sections": [
                {
                    "name": section.name,
                    "confidence": section.confidence,
                    "line_ids": section.line_ids,
                    "spatial_patterns": section.spatial_patterns,
                    "content_patterns": section.content_patterns,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "metadata": section.metadata,
                }
                for section in (self.sections or [])
            ],
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Receipt":
        """Create a Receipt instance from a dictionary."""
        return cls(
            receipt_id=data["receipt_id"],
            image_id=data["image_id"],
            words=[ReceiptWord(**word_data) for word_data in data["words"]],
            sections=[
                ReceiptSection(**section_data)
                for section_data in data.get("sections", [])
            ],
            metadata=data.get("metadata"),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if data.get("created_at")
                else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"])
                if data.get("updated_at")
                else None
            ),
        )
