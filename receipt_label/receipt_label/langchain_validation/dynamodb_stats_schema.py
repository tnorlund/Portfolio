"""
DynamoDB Schema for Validation Statistics

This module defines how to store word validation statistics in DynamoDB
to enable learning and confidence-based optimization over time.
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
import json


class WordValidationStatsEntity:
    """
    DynamoDB entity for word validation statistics.
    
    Primary Key Design:
    - PK: WORD#<word_text_upper>
    - SK: LABEL#<label_name>
    
    This allows efficient queries for:
    1. All labels for a specific word
    2. Specific word-label combination
    3. High-confidence word-label pairs (using GSI)
    
    GSI Design:
    - GSI1PK: CONFIDENCE#<bucket>  (e.g., CONFIDENCE#HIGH, CONFIDENCE#LOW)
    - GSI1SK: <confidence_score>#WORD#<word>#LABEL#<label>
    
    - GSI2PK: MERCHANT#<merchant_name>
    - GSI2SK: WORD#<word>#LABEL#<label>
    
    - GSI3PK: NEEDS_REVIEW#<true/false>
    - GSI3SK: LAST_VALIDATED#<timestamp>
    """
    
    def __init__(
        self,
        word_text: str,
        label: str,
        valid_count: int = 0,
        invalid_count: int = 0,
        suggested_corrections: Dict[str, int] = None,
        merchant_contexts: Dict[str, int] = None,
        last_validated: datetime = None,
        confidence_score: float = None
    ):
        self.word_text = word_text.upper()
        self.label = label
        self.valid_count = valid_count
        self.invalid_count = invalid_count
        self.suggested_corrections = suggested_corrections or {}
        self.merchant_contexts = merchant_contexts or {}
        self.last_validated = last_validated or datetime.now(timezone.utc)
        
        # Calculate confidence if not provided
        if confidence_score is None:
            self.confidence_score = self._calculate_confidence()
        else:
            self.confidence_score = confidence_score
    
    def _calculate_confidence(self) -> float:
        """Calculate confidence score with Laplace smoothing"""
        total = self.valid_count + self.invalid_count
        if total == 0:
            return 0.5
        alpha = 1.0  # Smoothing parameter
        return (self.valid_count + alpha) / (total + 2 * alpha)
    
    @property
    def needs_review(self) -> bool:
        """Determine if this word-label pair needs review"""
        total = self.valid_count + self.invalid_count
        if total < 5:
            return True
        if 0.3 <= self.confidence_score <= 0.7:
            return True
        if len(self.suggested_corrections) > 1:
            return True
        return False
    
    @property
    def confidence_bucket(self) -> str:
        """Get confidence bucket for GSI partitioning"""
        if self.confidence_score >= 0.9:
            return "HIGH"
        elif self.confidence_score >= 0.7:
            return "MEDIUM"
        elif self.confidence_score >= 0.3:
            return "MIXED"
        else:
            return "LOW"
    
    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format"""
        item = {
            # Primary key
            "PK": f"WORD#{self.word_text}",
            "SK": f"LABEL#{self.label}",
            
            # Core attributes
            "word_text": self.word_text,
            "label": self.label,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "total_validations": self.valid_count + self.invalid_count,
            "confidence_score": Decimal(str(round(self.confidence_score, 4))),
            "last_validated": self.last_validated.isoformat(),
            
            # Complex attributes (stored as JSON strings for DynamoDB)
            "suggested_corrections": json.dumps(self.suggested_corrections) if self.suggested_corrections else None,
            "merchant_contexts": json.dumps(self.merchant_contexts) if self.merchant_contexts else None,
            
            # GSI attributes
            "GSI1PK": f"CONFIDENCE#{self.confidence_bucket}",
            "GSI1SK": f"{self.confidence_score:.4f}#WORD#{self.word_text}#LABEL#{self.label}",
            
            # Needs review index
            "GSI3PK": f"NEEDS_REVIEW#{self.needs_review}",
            "GSI3SK": self.last_validated.isoformat(),
            
            # Metadata
            "TYPE": "WORD_VALIDATION_STATS",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add merchant index if we have merchant data
        if self.merchant_contexts:
            # Use the most common merchant for the index
            top_merchant = max(self.merchant_contexts.items(), key=lambda x: x[1])[0]
            item["GSI2PK"] = f"MERCHANT#{top_merchant}"
            item["GSI2SK"] = f"WORD#{self.word_text}#LABEL#{self.label}"
        
        # Remove None values
        return {k: v for k, v in item.items() if v is not None}
    
    @classmethod
    def from_dynamodb_item(cls, item: dict):
        """Create instance from DynamoDB item"""
        suggested_corrections = {}
        if item.get("suggested_corrections"):
            suggested_corrections = json.loads(item["suggested_corrections"])
        
        merchant_contexts = {}
        if item.get("merchant_contexts"):
            merchant_contexts = json.loads(item["merchant_contexts"])
        
        return cls(
            word_text=item["word_text"],
            label=item["label"],
            valid_count=item.get("valid_count", 0),
            invalid_count=item.get("invalid_count", 0),
            suggested_corrections=suggested_corrections,
            merchant_contexts=merchant_contexts,
            last_validated=datetime.fromisoformat(item["last_validated"]),
            confidence_score=float(item.get("confidence_score", 0.5))
        )


class ValidationStatsDynamoOperations:
    """
    DynamoDB operations for validation statistics.
    
    This would be integrated into the existing DynamoClient.
    """
    
    def __init__(self, table_name: str, boto3_client=None):
        """Initialize with DynamoDB table"""
        self.table_name = table_name
        if boto3_client:
            self.client = boto3_client
        else:
            import boto3
            self.client = boto3.client('dynamodb')
    
    def update_validation_stats(
        self,
        word_text: str,
        label: str,
        is_valid: bool,
        merchant: str = None,
        suggested_label: str = None
    ):
        """
        Update validation statistics for a word-label pair.
        
        Uses UpdateItem with atomic counters for thread-safe updates.
        """
        update_expression_parts = []
        expression_attribute_values = {}
        expression_attribute_names = {}
        
        # Update counters
        if is_valid:
            update_expression_parts.append("ADD valid_count :one")
        else:
            update_expression_parts.append("ADD invalid_count :one")
        expression_attribute_values[":one"] = {"N": "1"}
        
        # Update total
        update_expression_parts.append("ADD total_validations :one")
        
        # Update timestamp
        update_expression_parts.append("SET last_validated = :now")
        expression_attribute_values[":now"] = {"S": datetime.now(timezone.utc).isoformat()}
        
        # Update suggested corrections if invalid
        if not is_valid and suggested_label:
            update_expression_parts.append("SET suggested_corrections = :corrections")
            # This would need to fetch current and update - simplified here
            expression_attribute_values[":corrections"] = {
                "S": json.dumps({suggested_label: 1})
            }
        
        # Update merchant contexts
        if merchant:
            update_expression_parts.append("SET merchant_contexts = :merchants")
            # This would need to fetch current and update - simplified here
            expression_attribute_values[":merchants"] = {
                "S": json.dumps({merchant: 1})
            }
        
        # Perform update
        response = self.client.update_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"WORD#{word_text.upper()}"},
                "SK": {"S": f"LABEL#{label}"}
            },
            UpdateExpression=" ".join(update_expression_parts),
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW"
        )
        
        return response
    
    def get_word_stats(self, word_text: str, label: str = None) -> List[WordValidationStatsEntity]:
        """
        Get validation statistics for a word (optionally filtered by label).
        """
        if label:
            # Get specific word-label stats
            response = self.client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"WORD#{word_text.upper()}"},
                    "SK": {"S": f"LABEL#{label}"}
                }
            )
            if "Item" in response:
                return [WordValidationStatsEntity.from_dynamodb_item(
                    self._deserialize_item(response["Item"])
                )]
            return []
        else:
            # Get all labels for this word
            response = self.client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={
                    ":pk": {"S": f"WORD#{word_text.upper()}"}
                }
            )
            return [
                WordValidationStatsEntity.from_dynamodb_item(
                    self._deserialize_item(item)
                )
                for item in response.get("Items", [])
            ]
    
    def get_high_confidence_pairs(
        self,
        confidence_threshold: float = 0.9,
        min_validations: int = 10
    ) -> List[WordValidationStatsEntity]:
        """
        Get all word-label pairs with high confidence.
        
        Uses GSI1 for efficient querying.
        """
        response = self.client.query(
            TableName=self.table_name,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK >= :threshold",
            ExpressionAttributeValues={
                ":pk": {"S": "CONFIDENCE#HIGH"},
                ":threshold": {"S": f"{confidence_threshold:.4f}"}
            },
            FilterExpression="total_validations >= :min_val",
            ExpressionAttributeValues={
                ":min_val": {"N": str(min_validations)}
            }
        )
        
        return [
            WordValidationStatsEntity.from_dynamodb_item(
                self._deserialize_item(item)
            )
            for item in response.get("Items", [])
        ]
    
    def get_merchant_patterns(self, merchant: str) -> Dict[str, str]:
        """
        Get all word-label patterns for a specific merchant.
        
        Uses GSI2 for efficient merchant-based queries.
        """
        response = self.client.query(
            TableName=self.table_name,
            IndexName="GSI2",
            KeyConditionExpression="GSI2PK = :merchant",
            ExpressionAttributeValues={
                ":merchant": {"S": f"MERCHANT#{merchant}"}
            }
        )
        
        patterns = {}
        for item in response.get("Items", []):
            entity = WordValidationStatsEntity.from_dynamodb_item(
                self._deserialize_item(item)
            )
            if entity.confidence_score > 0.8:  # Only high-confidence patterns
                patterns[entity.word_text] = entity.label
        
        return patterns
    
    def get_items_needing_review(self, limit: int = 100) -> List[WordValidationStatsEntity]:
        """
        Get word-label pairs that need human review.
        
        Uses GSI3 for efficient querying of items needing review.
        """
        response = self.client.query(
            TableName=self.table_name,
            IndexName="GSI3",
            KeyConditionExpression="GSI3PK = :pk",
            ExpressionAttributeValues={
                ":pk": {"S": "NEEDS_REVIEW#True"}
            },
            Limit=limit,
            ScanIndexForward=False  # Most recent first
        )
        
        return [
            WordValidationStatsEntity.from_dynamodb_item(
                self._deserialize_item(item)
            )
            for item in response.get("Items", [])
        ]
    
    def _deserialize_item(self, dynamodb_item: dict) -> dict:
        """Convert DynamoDB item format to regular dict"""
        # Simplified deserialization - in production use boto3.dynamodb.types.TypeDeserializer
        result = {}
        for key, value in dynamodb_item.items():
            if "S" in value:
                result[key] = value["S"]
            elif "N" in value:
                result[key] = int(value["N"]) if "." not in value["N"] else float(value["N"])
            elif "BOOL" in value:
                result[key] = value["BOOL"]
            # Add other types as needed
        return result


# Example CloudFormation/CDK template addition for the GSIs
DYNAMODB_GSI_CONFIG = {
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "GSI1",
            "Keys": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
        },
        {
            "IndexName": "GSI2", 
            "Keys": [
                {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
        },
        {
            "IndexName": "GSI3",
            "Keys": [
                {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                {"AttributeName": "GSI3SK", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
        }
    ]
}