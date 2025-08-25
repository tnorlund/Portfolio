"""
Context Models
==============

Models for providing rich context to the LLM, including receipt structure,
merchant information, and word positioning data.

These models support the three-phase optimization approach:
- Context preparation (outside graph) uses these models
- LLM validation (minimal graph) receives formatted context
- Result processing (outside graph) updates based on validation
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field

from receipt_dynamo.entities import (
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptLine,
    ReceiptMetadata,
)
from receipt_label.constants import CORE_LABELS

# New dataclass models for the context preparation service
@dataclass
class WordContext:
    """Context about the specific word being validated"""
    target_word: ReceiptWord
    label_to_validate: str
    validation_status: str


@dataclass
class ReceiptContext:
    """Context about the receipt structure and metadata"""
    image_id: str
    receipt_id: int
    context_lines: List[ReceiptLine]
    target_line_id: int
    receipt_metadata: Optional[ReceiptMetadata] = None


@dataclass  
class SemanticContext:
    """Context from ChromaDB semantic similarity analysis"""
    word_embedding: List[float]
    chroma_id: str
    document_text: str
    metadata: Optional[Dict[str, Any]] = None
    similar_valid_words: List[Dict[str, Any]] = None
    similar_invalid_words: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.similar_valid_words is None:
            self.similar_valid_words = []
        if self.similar_invalid_words is None:
            self.similar_invalid_words = []


@dataclass
class ValidationContext:
    """Complete context for validating a single word label"""
    word_context: WordContext
    receipt_context: ReceiptContext
    semantic_context: SemanticContext
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'word_context': {
                'target_word': {
                    'text': self.word_context.target_word.text,
                    'image_id': self.word_context.target_word.image_id,
                    'receipt_id': self.word_context.target_word.receipt_id,
                    'line_id': self.word_context.target_word.line_id,
                    'word_id': self.word_context.target_word.word_id
                },
                'label_to_validate': self.word_context.label_to_validate,
                'validation_status': self.word_context.validation_status
            },
            'receipt_context': {
                'image_id': self.receipt_context.image_id,
                'receipt_id': self.receipt_context.receipt_id,
                'target_line_id': self.receipt_context.target_line_id,
                'context_lines': [
                    {'line_id': line.line_id, 'text': line.text} 
                    for line in self.receipt_context.context_lines
                ],
                'merchant_name': (
                    self.receipt_context.receipt_metadata.merchant_name 
                    if self.receipt_context.receipt_metadata else None
                )
            },
            'semantic_context': {
                'chroma_id': self.semantic_context.chroma_id,
                'similar_valid_count': len(self.semantic_context.similar_valid_words),
                'similar_invalid_count': len(self.semantic_context.similar_invalid_words)
            }
        }


# Legacy Pydantic models for backward compatibility


class ValidationTarget(BaseModel):
    """Single word to validate with rich context - Legacy model for backward compatibility"""
    
    id: str = Field(description="Unique identifier for tracking")
    text: str = Field(description="The word text to validate")
    proposed_label: str = Field(description="The label being validated")
    line_context: str = Field(
        description="The full line text containing this word"
    )
    
    # Position context
    line_position: int = Field(description="Line number in receipt")
    word_position_in_line: int = Field(description="Position within the line")
    receipt_position_percentile: float = Field(
        description="Position in receipt (0.0 = top, 1.0 = bottom)",
        ge=0.0,
        le=1.0
    )
    
    # Historical context
    previous_invalid_attempts: int = Field(
        default=0,
        description="Number of times this word's labels were marked invalid"
    )
    previous_invalid_labels: List[str] = Field(
        default=[],
        description="Labels that were previously marked invalid for this word"
    )

    @classmethod
    def from_validation_context(cls, context: ValidationContext) -> 'ValidationTarget':
        """Convert new ValidationContext to legacy ValidationTarget for compatibility"""
        target_word = context.word_context.target_word
        
        # Find the target line text
        target_line_text = ""
        for line in context.receipt_context.context_lines:
            if line.line_id == context.receipt_context.target_line_id:
                target_line_text = line.text
                break
        
        return cls(
            id=f"{target_word.image_id}#{target_word.receipt_id}#{target_word.line_id}#{target_word.word_id}",
            text=target_word.text,
            proposed_label=context.word_context.label_to_validate,
            line_context=target_line_text,
            line_position=target_word.line_id,
            word_position_in_line=target_word.word_id,
            receipt_position_percentile=0.5,  # TODO: Calculate from receipt structure
            previous_invalid_attempts=0,  # TODO: Track validation history
            previous_invalid_labels=[]  # TODO: Track previous attempts
        )


class BatchValidationContext(BaseModel):
    """Rich context from batch assembler for the LLM"""
    
    # Receipt metadata
    merchant_name: str = Field(description="Identified merchant name")
    merchant_category: Optional[str] = Field(
        default=None,
        description="Category of merchant (grocery, restaurant, etc.)"
    )
    receipt_id: int = Field(description="Receipt ID in database")
    image_id: str = Field(description="Image ID in database")
    
    # Validation targets with context
    targets: List[ValidationTarget] = Field(
        description="Words to validate with their context"
    )
    
    # Receipt structure
    receipt_text: str = Field(description="Full formatted receipt text")
    total_lines: int = Field(description="Total number of lines in receipt")
    total_words: int = Field(description="Total number of words in receipt")
    
    # Available labels
    available_labels: List[str] = Field(
        default_factory=lambda: list(CORE_LABELS.keys()),
        description="Valid labels that can be assigned"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "merchant_name": "CVS Pharmacy",
                "merchant_category": "pharmacy",
                "receipt_id": 12345,
                "image_id": "IMG_20240101_001",
                "targets": [
                    {
                        "id": "TARGET_001",
                        "text": "$12.99",
                        "proposed_label": "GRAND_TOTAL",
                        "line_context": "TOTAL         $12.99",
                        "line_position": 15,
                        "word_position_in_line": 2,
                        "receipt_position_percentile": 0.89,
                        "similar_valid_examples": [
                            {
                                "text": "$15.99",
                                "merchant": "CVS",
                                "distance": 0.12,
                                "label_status": "VALID"
                            }
                        ],
                        "previous_invalid_attempts": 0
                    }
                ],
                "receipt_text": "CVS PHARMACY\\nSTORE #1234\\n...\\nTOTAL         $12.99",
                "total_lines": 20,
                "total_words": 45
            }
        }