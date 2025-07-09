# Currency Analysis Entity Design

## Overview

This document specifies the design for a new `ReceiptCurrencyAnalysis` entity that aligns with the existing DynamoDB schema and entity patterns in the `receipt_dynamo` package.

## Current Entity Pattern Analysis

### **Existing Analysis Entities**
- **Receipt Structure Analysis**: Identifies sections (header, body, footer)
- **Receipt Label Analysis**: Labels individual words/fields
- **Receipt Line Item Analysis**: Identifies line items and financial totals
- **Receipt Validation**: Validates receipt data and identifies issues

### **Common Design Patterns**
1. **Core Structure**: `image_id`, `receipt_id`, `version`, `timestamp_added`
2. **Reasoning-Based**: Detailed textual explanations instead of confidence scores
3. **Metadata**: Standardized processing metrics, history, and source info
4. **DynamoDB Keys**: Consistent PK/SK patterns with GSI support
5. **Type Safety**: Proper type annotations and validation

## Currency Analysis Entity Design

### **Purpose**
Analyze currency patterns and amounts in receipts to support enhanced line item processing and financial validation.

### **Entity Structure**

#### **Core Data Classes**

```python
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from uuid import UUID

@dataclass
class CurrencyCandidate:
    """Represents a potential currency amount found in the receipt"""
    candidate_id: str
    word_id: int
    line_id: int
    text: str
    amount: Decimal
    currency_symbol: str
    currency_code: str
    classification: str  # "line_item", "subtotal", "tax", "total", "discount", "fee", "tip"

    # Enhanced fields for financial validation
    quantity: Optional[int] = None
    unit_price: Optional[Decimal] = None
    extended_price: Optional[Decimal] = None
    left_description: Optional[str] = None

    # Spatial context
    x_position: float
    y_position: float
    bounding_box: Optional[Dict[str, Any]] = None

    # Analysis metadata
    reasoning: str
    confidence: float
    patterns_matched: List[str] = field(default_factory=list)

    # Relationships
    calculation_components: Optional[List[str]] = None  # For subtotal/total that reference other candidates

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for DynamoDB storage"""
        return {
            'candidate_id': self.candidate_id,
            'word_id': self.word_id,
            'line_id': self.line_id,
            'text': self.text,
            'amount': str(self.amount),
            'currency_symbol': self.currency_symbol,
            'currency_code': self.currency_code,
            'classification': self.classification,
            'quantity': self.quantity,
            'unit_price': str(self.unit_price) if self.unit_price else None,
            'extended_price': str(self.extended_price) if self.extended_price else None,
            'left_description': self.left_description,
            'x_position': self.x_position,
            'y_position': self.y_position,
            'bounding_box': self.bounding_box,
            'reasoning': self.reasoning,
            'confidence': self.confidence,
            'patterns_matched': self.patterns_matched,
            'calculation_components': self.calculation_components
        }

@dataclass
class CurrencyPattern:
    """Represents a currency pattern detected in the receipt"""
    pattern_type: str  # "basic_currency", "quantity_price", "percentage", "tax_rate"
    pattern_regex: str
    description: str
    examples: List[str]
    matches_found: int
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            'pattern_type': self.pattern_type,
            'pattern_regex': self.pattern_regex,
            'description': self.description,
            'examples': self.examples,
            'matches_found': self.matches_found,
            'confidence': self.confidence
        }

@dataclass
class FinancialSummary:
    """Summarizes financial information extracted from currency analysis"""
    line_items_total: Decimal
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    discounts: Decimal
    tips: Decimal
    fees: Decimal

    # Validation fields
    line_items_count: int
    subtotal_found: bool
    tax_found: bool
    total_found: bool

    # Calculated fields
    estimated_tax_rate: Optional[float] = None
    total_discrepancy: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'line_items_total': str(self.line_items_total),
            'subtotal': str(self.subtotal),
            'tax': str(self.tax),
            'total': str(self.total),
            'discounts': str(self.discounts),
            'tips': str(self.tips),
            'fees': str(self.fees),
            'line_items_count': self.line_items_count,
            'subtotal_found': self.subtotal_found,
            'tax_found': self.tax_found,
            'total_found': self.total_found,
            'estimated_tax_rate': self.estimated_tax_rate,
            'total_discrepancy': str(self.total_discrepancy) if self.total_discrepancy else None
        }
```

#### **Main Entity Class**

```python
@dataclass
class ReceiptCurrencyAnalysis:
    """Main currency analysis entity following existing patterns"""

    # Core identifiers (following existing pattern)
    image_id: str
    receipt_id: int
    version: str = "1.0.0"

    # Analysis results
    currency_candidates: List[CurrencyCandidate]
    currency_patterns: List[CurrencyPattern]
    financial_summary: FinancialSummary

    # Primary currency information
    primary_currency_code: str = "USD"
    primary_currency_symbol: str = "$"

    # Analysis metadata (following existing pattern)
    overall_reasoning: str
    total_candidates_found: int
    classification_accuracy: float

    # Standard fields (following existing pattern)
    timestamp_added: datetime
    timestamp_updated: Optional[datetime] = None

    # Metadata (following existing pattern)
    processing_metrics: Dict[str, Any] = field(default_factory=dict)
    processing_history: List[Dict[str, Any]] = field(default_factory=list)
    source_information: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for DynamoDB storage"""
        return {
            'image_id': self.image_id,
            'receipt_id': self.receipt_id,
            'version': self.version,
            'currency_candidates': [candidate.to_dict() for candidate in self.currency_candidates],
            'currency_patterns': [pattern.to_dict() for pattern in self.currency_patterns],
            'financial_summary': self.financial_summary.to_dict(),
            'primary_currency_code': self.primary_currency_code,
            'primary_currency_symbol': self.primary_currency_symbol,
            'overall_reasoning': self.overall_reasoning,
            'total_candidates_found': self.total_candidates_found,
            'classification_accuracy': self.classification_accuracy,
            'timestamp_added': self.timestamp_added.isoformat(),
            'timestamp_updated': self.timestamp_updated.isoformat() if self.timestamp_updated else None,
            'processing_metrics': self.processing_metrics,
            'processing_history': self.processing_history,
            'source_information': self.source_information
        }
```

## DynamoDB Schema Integration

### **Table Schema Alignment**

Following the existing pattern from the README.md table design:

| Field | Value |
|-------|-------|
| **PK** | `IMAGE#{image_id}` |
| **SK** | `RECEIPT#{receipt_id:05d}#ANALYSIS#CURRENCY#{version}` |
| **GSI1 PK** | `ANALYSIS_TYPE` |
| **GSI1 SK** | `CURRENCY#{timestamp}` |
| **GSI2 PK** | `RECEIPT` |
| **GSI2 SK** | `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#ANALYSIS#CURRENCY` |
| **GSI3 PK** | `CURRENCY_STATUS#{classification_accuracy}` |
| **GSI3 SK** | `TIMESTAMP#{timestamp}` |
| **TYPE** | `RECEIPT_CURRENCY_ANALYSIS` |

### **Attributes**
- `currency_candidates`: List of currency candidates with spatial and classification data
- `currency_patterns`: Detected currency patterns and their effectiveness
- `financial_summary`: Aggregated financial information
- `primary_currency_code`: Main currency used (e.g., "USD")
- `primary_currency_symbol`: Main currency symbol (e.g., "$")
- `overall_reasoning`: Detailed explanation of currency analysis
- `total_candidates_found`: Count of currency candidates
- `classification_accuracy`: Accuracy of currency classification
- `timestamp_added`: Creation timestamp
- `timestamp_updated`: Last update timestamp
- `version`: Analysis version for tracking improvements
- `processing_metrics`: Standard processing metrics
- `processing_history`: Standard processing history
- `source_information`: Standard source information

## Storage and Retrieval Patterns

### **Storage Functions**

```python
def store_receipt_currency_analysis(
    dynamodb_client,
    table_name: str,
    analysis: ReceiptCurrencyAnalysis
) -> None:
    """Store currency analysis following existing patterns"""

    item = {
        'PK': {'S': f'IMAGE#{analysis.image_id}'},
        'SK': {'S': f'RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#CURRENCY#{analysis.version}'},
        'GSI1PK': {'S': 'ANALYSIS_TYPE'},
        'GSI1SK': {'S': f'CURRENCY#{analysis.timestamp_added.isoformat()}'},
        'GSI2PK': {'S': 'RECEIPT'},
        'GSI2SK': {'S': f'IMAGE#{analysis.image_id}#RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#CURRENCY'},
        'GSI3PK': {'S': f'CURRENCY_STATUS#{analysis.classification_accuracy}'},
        'GSI3SK': {'S': f'TIMESTAMP#{analysis.timestamp_added.isoformat()}'},
        'TYPE': {'S': 'RECEIPT_CURRENCY_ANALYSIS'},
        **_convert_to_dynamodb_types(analysis.to_dict())
    }

    dynamodb_client.put_item(TableName=table_name, Item=item)

def get_receipt_currency_analysis(
    dynamodb_client,
    table_name: str,
    image_id: str,
    receipt_id: int,
    version: str = "1.0.0"
) -> Optional[ReceiptCurrencyAnalysis]:
    """Retrieve currency analysis following existing patterns"""

    response = dynamodb_client.get_item(
        TableName=table_name,
        Key={
            'PK': {'S': f'IMAGE#{image_id}'},
            'SK': {'S': f'RECEIPT#{receipt_id:05d}#ANALYSIS#CURRENCY#{version}'}
        }
    )

    if 'Item' not in response:
        return None

    return _convert_from_dynamodb_types(response['Item'])
```

### **Query Patterns**

```python
# Get all currency analyses for a receipt
def get_all_currency_analyses_for_receipt(
    dynamodb_client,
    table_name: str,
    image_id: str,
    receipt_id: int
) -> List[ReceiptCurrencyAnalysis]:
    """Get all currency analysis versions for a receipt"""

    response = dynamodb_client.query(
        TableName=table_name,
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk_prefix)',
        ExpressionAttributeValues={
            ':pk': {'S': f'IMAGE#{image_id}'},
            ':sk_prefix': {'S': f'RECEIPT#{receipt_id:05d}#ANALYSIS#CURRENCY'}
        }
    )

    return [_convert_from_dynamodb_types(item) for item in response['Items']]

# Get currency analyses by accuracy threshold
def get_currency_analyses_by_accuracy(
    dynamodb_client,
    table_name: str,
    min_accuracy: float
) -> List[ReceiptCurrencyAnalysis]:
    """Get currency analyses above accuracy threshold"""

    response = dynamodb_client.query(
        TableName=table_name,
        IndexName='GSI3',
        KeyConditionExpression='GSI3PK = :pk',
        FilterExpression='classification_accuracy >= :min_accuracy',
        ExpressionAttributeValues={
            ':pk': {'S': f'CURRENCY_STATUS#{min_accuracy}'},
            ':min_accuracy': {'N': str(min_accuracy)}
        }
    )

    return [_convert_from_dynamodb_types(item) for item in response['Items']]
```

## Integration with Existing Entities

### **Line Item Analysis Enhancement**
```python
# Enhanced line item analysis can reference currency analysis
class EnhancedLineItemAnalysis:
    # ... existing fields ...
    currency_analysis_version: str = "1.0.0"
    currency_candidate_mapping: Dict[str, str] = field(default_factory=dict)  # item_id -> candidate_id

    def link_to_currency_analysis(self, currency_analysis: ReceiptCurrencyAnalysis):
        """Link line items to currency candidates"""
        self.currency_analysis_version = currency_analysis.version

        for item in self.line_items:
            # Find matching currency candidate
            matching_candidate = self._find_matching_candidate(item, currency_analysis)
            if matching_candidate:
                self.currency_candidate_mapping[item.item_id] = matching_candidate.candidate_id
```

### **Financial Validation Integration**
```python
# Financial validation can use currency analysis
class FinancialValidator:
    def validate_using_currency_analysis(self, currency_analysis: ReceiptCurrencyAnalysis):
        """Use currency analysis for comprehensive validation"""

        # Validate line item math using currency candidates
        line_item_candidates = [c for c in currency_analysis.currency_candidates
                               if c.classification == 'line_item']

        for candidate in line_item_candidates:
            if candidate.quantity and candidate.unit_price:
                expected_total = candidate.quantity * candidate.unit_price
                if abs(expected_total - candidate.extended_price) > 0.01:
                    # Flag discrepancy
                    pass

        # Validate financial summary
        financial_summary = currency_analysis.financial_summary
        calculated_total = (financial_summary.subtotal +
                           financial_summary.tax +
                           financial_summary.fees -
                           financial_summary.discounts)

        if abs(calculated_total - financial_summary.total) > 0.01:
            # Flag total discrepancy
            pass
```

## Key Benefits

### **1. Alignment with Existing Patterns**
- Same naming convention: `ReceiptCurrencyAnalysis`
- Same key structure: PK/SK with proper GSI support
- Same metadata pattern: processing metrics, history, source info
- Same reasoning pattern: detailed explanations for decisions

### **2. Enhanced Financial Processing**
- Supports comprehensive line item validation
- Enables quantity × unit_price = extended_price validation
- Provides foundation for financial validation thumbs up/down

### **3. Spatial Context Support**
- Includes bounding box information for spatial analysis
- Supports left_description extraction for line items
- Enables multi-line description grouping

### **4. Flexible Classification**
- Supports multiple currency types (line_item, subtotal, tax, total, etc.)
- Includes confidence scoring for classification accuracy
- Enables pattern-based enhancement over time

### **5. Version Management**
- Follows existing versioning patterns
- Supports analysis evolution and improvement
- Enables A/B testing of currency detection approaches

This design maintains full compatibility with the existing DynamoDB schema while providing the currency analysis capabilities needed for enhanced line item processing and financial validation.
