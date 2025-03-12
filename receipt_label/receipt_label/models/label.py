from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Generator, Tuple
from decimal import Decimal
from datetime import datetime
import uuid

from .position import BoundingBox, Point
from .metadata import MetadataMixin
from receipt_dynamo.entities.receipt_label_analysis import ReceiptLabelAnalysis as DynamoReceiptLabelAnalysis

@dataclass
class WordLabel:
    """
    Represents a label applied to a word in a receipt.
    
    Instead of using confidence scores, this class includes detailed reasoning
    explaining why a particular label was applied to a word.
    """
    text: str
    label: str
    line_id: int
    word_id: int
    reasoning: str
    section_name: Optional[str] = None
    bounding_box: Optional[BoundingBox] = None
    position: Optional[Dict] = None  # Deprecated: Use bounding_box instead

    def __post_init__(self):
        # Convert dictionary bounding_box to BoundingBox object if needed
        if isinstance(self.bounding_box, dict) and self.bounding_box:
            self.bounding_box = BoundingBox.from_dict(self.bounding_box)
        elif self.bounding_box is None:
            self.bounding_box = None
            
        # For backward compatibility
        if self.position is None:
            self.position = {}
    
    def to_dict(self) -> Dict:
        """Convert the WordLabel to a dictionary for serialization."""
        result = {
            "text": self.text,
            "label": self.label,
            "line_id": self.line_id,
            "word_id": self.word_id,
            "reasoning": self.reasoning,
        }
        
        if self.section_name:
            result["section_name"] = self.section_name
            
        if self.bounding_box:
            result["bounding_box"] = self.bounding_box.to_dict()
            
        return result
    
    @classmethod
    def from_dict(cls, data: Dict) -> "WordLabel":
        """Create a WordLabel from a dictionary."""
        bounding_box_data = data.get("bounding_box")
        bounding_box = None
        if bounding_box_data:
            bounding_box = BoundingBox.from_dict(bounding_box_data)
            
        return cls(
            text=data.get("text", ""),
            label=data.get("label", ""),
            line_id=data.get("line_id", 0),
            word_id=data.get("word_id", 0),
            reasoning=data.get("reasoning", ""),
            section_name=data.get("section_name"),
            bounding_box=bounding_box
        )


@dataclass
class FieldGroup:
    """
    Represents a group of related words that form a semantic field.
    
    For example, a "business_name" field might consist of multiple WordLabels
    that together form the complete business name.
    """
    field_type: str
    words: List[WordLabel]
    reasoning: str = ""
    metadata: Dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if not self.reasoning and self.words:
            word_reasonings = [word.reasoning for word in self.words if word.reasoning]
            if word_reasonings:
                self.reasoning = f"Field composed of {len(self.words)} words: {' '.join(word_reasonings[:3])}..."

    @property
    def text(self) -> str:
        """Get the complete text of the field by joining all word texts."""
        return " ".join(word.text for word in self.words)


@dataclass
class SectionLabels:
    """
    Represents all labeled words within a section of a receipt.
    
    Each section (e.g., header, body, footer) has its own set of labeled words
    and section-specific reasoning.
    """
    section_name: str
    words: List[WordLabel]
    reasoning: str = ""
    requires_review: bool = False
    review_reasons: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def get_fields_by_type(self, field_type: str) -> List[WordLabel]:
        """Get all words with a specific label type in this section."""
        return [word for word in self.words if word.label == field_type]

    def generate_field_groups(self) -> List[FieldGroup]:
        """
        Group labeled words into semantic field groups based on label type.
        
        Returns:
            List[FieldGroup]: List of field groups created from the labels
        """
        # Get unique label types
        label_types = set(word.label for word in self.words)
        
        field_groups = []
        for label_type in label_types:
            matching_words = self.get_fields_by_type(label_type)
            if matching_words:
                field_groups.append(
                    FieldGroup(
                        field_type=label_type,
                        words=matching_words,
                        reasoning=f"Group of {len(matching_words)} words labeled as {label_type}"
                    )
                )
        
        return field_groups


@dataclass
class LabelAnalysis(MetadataMixin):
    """
    Comprehensive analysis of labeled words in a receipt.
    
    This class stores the results of field labeling across all receipt sections,
    with detailed reasoning about how and why words were labeled as they were.
    It provides methods for accessing and grouping labels in various ways.
    
    Instead of using confidence scores, this class relies on detailed textual
    reasoning to explain labeling decisions.
    """
    labels: List[WordLabel]
    sections: List[SectionLabels] = field(default_factory=list)
    total_labeled_words: int = 0
    requires_review: bool = False
    review_reasons: List[str] = field(default_factory=list)
    analysis_reasoning: str = ""
    metadata: Dict = field(default_factory=dict)
    timestamp_added: Optional[str] = None
    timestamp_updated: Optional[str] = None

    def __post_init__(self):
        if self.total_labeled_words == 0:
            self.total_labeled_words = len(self.labels)
        
        # If no reasoning is provided, generate a basic one
        if not self.analysis_reasoning:
            self.analysis_reasoning = self.generate_reasoning()
            
        # Initialize metadata
        self.initialize_metadata()
        
        # Add analysis-specific metrics
        self.add_processing_metric("total_words", self.total_labeled_words)
        self.add_processing_metric("section_count", len(self.sections))
        
        # If requires review, add to history
        if self.requires_review:
            self.add_history_event("flagged_for_review", {
                "reasons": self.review_reasons
            })
    
    def generate_reasoning(self) -> str:
        """
        Generate a comprehensive reasoning explanation for the label analysis.
        
        Returns:
            str: A detailed explanation of how words were labeled
        """
        reasoning_parts = [
            f"Analyzed {self.total_labeled_words} words across {len(self.sections)} sections."
        ]
        
        # Add section summaries
        section_parts = []
        for section in self.sections:
            label_types = set(word.label for word in section.words)
            section_parts.append(
                f"{section.section_name}: {len(section.words)} words with {len(label_types)} label types"
            )
        
        if section_parts:
            reasoning_parts.append("Section summary: " + "; ".join(section_parts))
        
        # Add review reasons if any
        if self.requires_review:
            reasoning_parts.append(
                f"Analysis requires review for {len(self.review_reasons)} reasons: "
                + "; ".join(self.review_reasons[:3])
            )
        
        return " ".join(reasoning_parts)
    
    def get_labels_by_type(self, label_type: str) -> List[WordLabel]:
        """Get all words with a specific label type."""
        return [label for label in self.labels if label.label == label_type]
    
    def get_field_groups(self) -> List[FieldGroup]:
        """
        Group all labeled words into semantic field groups across all sections.
        
        Returns:
            List[FieldGroup]: List of field groups created from the labels
        """
        # Get unique label types
        label_types = set(word.label for word in self.labels)
        
        field_groups = []
        for label_type in label_types:
            matching_words = self.get_labels_by_type(label_type)
            if matching_words:
                field_groups.append(
                    FieldGroup(
                        field_type=label_type,
                        words=matching_words,
                        reasoning=f"Group of {len(matching_words)} words labeled as {label_type}"
                    )
                )
        
        return field_groups
    
    def get_section_by_name(self, section_name: str) -> Optional[SectionLabels]:
        """Get a section by its name."""
        for section in self.sections:
            if section.section_name.lower() == section_name.lower():
                return section
        return None
    
    def extract_field_text(self, field_type: str) -> str:
        """
        Extract the full text of a field by combining all words with the given label type.
        
        Args:
            field_type (str): The label type to extract (e.g., "business_name")
            
        Returns:
            str: The combined text of all words with the specified label
        """
        matching_words = self.get_labels_by_type(field_type)
        if not matching_words:
            return ""
        
        # Sort by line_id and word_id to maintain original order
        sorted_words = sorted(matching_words, key=lambda w: (w.line_id, w.word_id))
        
        # Group by line_id
        lines = {}
        for word in sorted_words:
            if word.line_id not in lines:
                lines[word.line_id] = []
            lines[word.line_id].append(word)
        
        # Combine text by line
        result = []
        for line_id in sorted(lines.keys()):
            line_text = " ".join(word.text for word in lines[line_id])
            result.append(line_text)
        
        return " ".join(result)
    
    def to_dynamo(self, image_id: str, receipt_id: int) -> DynamoReceiptLabelAnalysis:
        """
        Convert the LabelAnalysis instance to a DynamoReceiptLabelAnalysis instance for DynamoDB storage.
        
        This method transforms the LabelAnalysis object into a DynamoReceiptLabelAnalysis instance
        that can be directly stored in DynamoDB via the DynamoDB client.
        
        Args:
            image_id (str): The ID of the image
            receipt_id (int): The ID of the receipt
            
        Returns:
            DynamoReceiptLabelAnalysis: An instance ready for DynamoDB storage
        """
        # Convert labels to dictionaries
        labels_list = []
        for label in self.labels:
            label_dict = {
                "label_type": label.label,
                "line_id": label.line_id,
                "word_id": label.word_id,
                "text": label.text,
                "reasoning": label.reasoning,
            }
            
            if label.section_name:
                label_dict["section_name"] = label.section_name
                
            if label.bounding_box:
                label_dict["bounding_box"] = label.bounding_box.to_dict()
                
            labels_list.append(label_dict)
        
        # Get the current timestamp if not provided
        timestamp = self.timestamp_added
        if not timestamp:
            timestamp = datetime.now()
        
        # Create the DynamoReceiptLabelAnalysis instance
        return DynamoReceiptLabelAnalysis(
            image_id=image_id,
            receipt_id=receipt_id,
            labels=labels_list,
            timestamp_added=timestamp,
            version=self.metadata.get("version", "1.0"),
            overall_reasoning=self.analysis_reasoning,
            metadata=self.metadata
        )
    
    def to_dict(self) -> Dict:
        """
        Convert the LabelAnalysis instance to a dictionary for serialization.
        
        Returns:
            Dict: A dictionary representation suitable for serialization
        """
        # Convert labels to dictionaries
        labels_dicts = [label.to_dict() for label in self.labels]
        
        # Convert sections to dictionaries
        sections_dicts = []
        for section in self.sections:
            section_dict = {
                "section_name": section.section_name,
                "words": [word.to_dict() for word in section.words],
                "reasoning": section.reasoning,
                "requires_review": section.requires_review,
                "review_reasons": section.review_reasons,
            }
            
            if section.metadata:
                section_dict["metadata"] = section.metadata
                
            sections_dicts.append(section_dict)
        
        # Build the main dictionary
        result = {
            "labels": labels_dicts,
            "sections": sections_dicts,
            "total_labeled_words": self.total_labeled_words,
            "requires_review": self.requires_review,
            "review_reasons": self.review_reasons,
            "analysis_reasoning": self.analysis_reasoning,
            "metadata": self.metadata,
        }
        
        # Add timestamps if they exist
        if self.timestamp_added:
            result["timestamp_added"] = self.timestamp_added
            
        if self.timestamp_updated:
            result["timestamp_updated"] = self.timestamp_updated
            
        return result
    
    @classmethod
    def from_dynamo(cls, data: Dict) -> "LabelAnalysis":
        """
        Create a LabelAnalysis instance from DynamoDB data.
        
        This method reconstructs a LabelAnalysis object and all its nested objects
        from a dictionary structure retrieved from DynamoDB.
        
        Args:
            data (Dict): The DynamoDB data dictionary
            
        Returns:
            LabelAnalysis: A new instance populated with the DynamoDB data
        """
        # Convert label dictionaries back to WordLabel objects
        labels = []
        for label_data in data.get("labels", []):
            labels.append(WordLabel.from_dict(label_data))
        
        # Convert section dictionaries back to SectionLabels objects
        sections = []
        for section_data in data.get("sections", []):
            section_words = [WordLabel.from_dict(word_data) for word_data in section_data.get("words", [])]
            sections.append(
                SectionLabels(
                    section_name=section_data.get("section_name", ""),
                    words=section_words,
                    reasoning=section_data.get("reasoning", ""),
                    requires_review=section_data.get("requires_review", False),
                    review_reasons=section_data.get("review_reasons", []),
                    metadata=section_data.get("metadata", {})
                )
            )
        
        # Create the LabelAnalysis instance
        return cls(
            labels=labels,
            sections=sections,
            total_labeled_words=data.get("total_labeled_words", len(labels)),
            requires_review=data.get("requires_review", False),
            review_reasons=data.get("review_reasons", []),
            analysis_reasoning=data.get("analysis_reasoning", ""),
            metadata=data.get("metadata", {}),
            timestamp_added=data.get("timestamp_added"),
            timestamp_updated=data.get("timestamp_updated")
        )
    
    @classmethod
    def from_gpt_response(cls, response_data: Dict) -> "LabelAnalysis":
        """
        Create a LabelAnalysis instance from GPT API response data.
        
        Args:
            response_data (Dict): The processed response from gpt_request_field_labeling
            
        Returns:
            LabelAnalysis: A new instance populated with the response data
        """
        labels = []
        for label_data in response_data.get("labels", []):
            bounding_box_data = label_data.get("bounding_box")
            bounding_box = None
            if bounding_box_data:
                bounding_box = BoundingBox.from_dict(bounding_box_data)
            
            # Convert label to uppercase to ensure consistency
            label_value = label_data.get("label", "")
            if label_value:
                label_value = label_value.upper()
            
            labels.append(
                WordLabel(
                    text=label_data.get("text", ""),
                    label=label_value,
                    line_id=label_data.get("line_id", 0),
                    word_id=label_data.get("word_id", 0),
                    reasoning=label_data.get("reasoning", ""),
                    section_name=label_data.get("section_name", None),
                    bounding_box=bounding_box
                )
            )
        
        metadata = response_data.get("metadata", {})
        
        return cls(
            labels=labels,
            total_labeled_words=metadata.get("total_labeled_words", len(labels)),
            requires_review=metadata.get("requires_review", False),
            review_reasons=metadata.get("review_reasons", []),
            analysis_reasoning=metadata.get("analysis_reasoning", ""),
            metadata=metadata
        ) 