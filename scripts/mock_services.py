"""
Mock services for local testing of the agent-based receipt labeling system.

This module provides mock implementations of AWS and external services
to enable local development and testing without incurring costs.
"""

import asyncio
import json
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

from receipt_dynamo.entities import (
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)


class MockDynamoClient:
    """Mock DynamoDB client for local testing."""

    def __init__(self):
        """Initialize mock storage."""
        self.receipt_metadata = {}
        self.receipt_words = {}
        self.receipt_lines = {}
        self.receipt_labels = {}
        self.ai_usage_metrics = []

    def get_receipt_metadata(
        self, receipt_id: str
    ) -> Optional[ReceiptMetadata]:
        """Mock getting receipt metadata."""
        return self.receipt_metadata.get(receipt_id)

    def put_receipt_metadata(self, metadata: ReceiptMetadata) -> None:
        """Mock storing receipt metadata."""
        self.receipt_metadata[str(metadata.receipt_id)] = metadata

    def list_receipt_words_by_receipt(
        self, receipt_id: str
    ) -> List[ReceiptWord]:
        """Mock listing receipt words."""
        return self.receipt_words.get(receipt_id, [])

    def list_receipt_lines_by_receipt(
        self, receipt_id: str
    ) -> List[ReceiptLine]:
        """Mock listing receipt lines."""
        return self.receipt_lines.get(receipt_id, [])

    def batch_put_receipt_word_labels(
        self, labels: List[ReceiptWordLabel]
    ) -> None:
        """Mock batch storing labels."""
        for label in labels:
            key = f"{label.receipt_id}:{label.word_id}"
            self.receipt_labels[key] = label

    def put_ai_usage_metric(self, metric: Dict[str, Any]) -> None:
        """Mock storing AI usage metrics."""
        self.ai_usage_metrics.append(metric)

    def add_test_receipt(
        self,
        receipt_id: str,
        words: List[ReceiptWord],
        lines: List[ReceiptLine],
        metadata: Optional[ReceiptMetadata] = None,
    ) -> None:
        """Add test data for a receipt."""
        self.receipt_words[receipt_id] = words
        self.receipt_lines[receipt_id] = lines
        if metadata:
            self.receipt_metadata[receipt_id] = metadata


class MockOpenAIClient:
    """Mock OpenAI client for local testing."""

    def __init__(self, mock_responses: Optional[Dict[str, Any]] = None):
        """Initialize with optional mock responses."""
        self.mock_responses = mock_responses or {}
        self.api_calls = []

    def create_completion(
        self, model: str, messages: List[Dict], **kwargs
    ) -> Dict:
        """Mock OpenAI completion."""
        self.api_calls.append(
            {
                "model": model,
                "messages": messages,
                "timestamp": datetime.utcnow(),
                "kwargs": kwargs,
            }
        )

        # Generate mock response
        if "label_receipt" in str(messages):
            return self._mock_labeling_response()
        return {"choices": [{"message": {"content": "Mock response"}}]}

    def _mock_labeling_response(self) -> Dict:
        """Generate mock labeling response."""
        labels = [
            {"word_id": 1, "label": "MERCHANT_NAME", "confidence": 0.95},
            {"word_id": 5, "label": "DATE", "confidence": 0.90},
            {"word_id": 10, "label": "GRAND_TOTAL", "confidence": 0.98},
            {"word_id": 7, "label": "PRODUCT_NAME", "confidence": 0.85},
        ]
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"labels": labels}),
                    }
                }
            ],
            "usage": {"total_tokens": 150},
        }

    def create_batch(self, requests: List[Dict]) -> Dict:
        """Mock batch creation."""
        batch_id = f"batch_{int(time.time())}"
        self.api_calls.append(
            {
                "type": "batch",
                "batch_id": batch_id,
                "requests": requests,
                "timestamp": datetime.utcnow(),
            }
        )
        return {"id": batch_id, "status": "validating"}


class MockPineconeClient:
    """Mock Pinecone client for local testing."""

    def __init__(self):
        """Initialize mock vector storage."""
        self.vectors = {}
        self.queries = []

    def upsert(self, vectors: List[Dict]) -> Dict:
        """Mock vector upsert."""
        for vector in vectors:
            self.vectors[vector["id"]] = vector
        return {"upserted_count": len(vectors)}

    def query(
        self,
        vector: List[float],
        top_k: int = 10,
        filter: Optional[Dict] = None,
        **kwargs,
    ) -> Dict:
        """Mock vector query."""
        self.queries.append(
            {
                "vector": vector,
                "top_k": top_k,
                "filter": filter,
                "timestamp": datetime.utcnow(),
            }
        )

        # Return mock similar patterns
        if filter and filter.get("merchant_name"):
            return {
                "matches": [
                    {
                        "id": "pattern_1",
                        "score": 0.95,
                        "metadata": {
                            "label": "PRODUCT_NAME",
                            "text": "COFFEE",
                            "confidence": 0.90,
                        },
                    },
                    {
                        "id": "pattern_2",
                        "score": 0.92,
                        "metadata": {
                            "label": "TAX",
                            "text": "TAX",
                            "confidence": 0.88,
                        },
                    },
                ]
            }
        return {"matches": []}


class MockClientManager:
    """Mock client manager for local testing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize mock clients."""
        self.config = config or {}
        self.dynamo = MockDynamoClient()
        self.openai_client = MockOpenAIClient()
        self.pinecone_client = MockPineconeClient()

        # Mock methods
        self.get_openai_client = Mock(return_value=self.openai_client)
        self.get_pinecone_index = Mock(return_value=self.pinecone_client)


def create_sample_receipt(
    receipt_id: str = "test_001", merchant_name: str = "WALMART"
) -> tuple[List[ReceiptWord], List[ReceiptLine], ReceiptMetadata]:
    """Create a sample receipt for testing."""
    words = [
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=1,
            word_id=1,
            text=merchant_name,
            bounding_box={"x": 100, "y": 50, "width": 200, "height": 30},
            top_left={"x": 100, "y": 50},
            top_right={"x": 300, "y": 50},
            bottom_left={"x": 100, "y": 80},
            bottom_right={"x": 300, "y": 80},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.98,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=1,
            word_id=2,
            text="SUPERCENTER",
            bounding_box={"x": 310, "y": 50, "width": 150, "height": 30},
            top_left={"x": 310, "y": 50},
            top_right={"x": 460, "y": 50},
            bottom_left={"x": 310, "y": 80},
            bottom_right={"x": 460, "y": 80},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.97,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=2,
            word_id=3,
            text="123",
            bounding_box={"x": 100, "y": 100, "width": 50, "height": 25},
            top_left={"x": 100, "y": 100},
            top_right={"x": 150, "y": 100},
            bottom_left={"x": 100, "y": 125},
            bottom_right={"x": 150, "y": 125},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=2,
            word_id=4,
            text="MAIN",
            bounding_box={"x": 160, "y": 100, "width": 60, "height": 25},
            top_left={"x": 160, "y": 100},
            top_right={"x": 220, "y": 100},
            bottom_left={"x": 160, "y": 125},
            bottom_right={"x": 220, "y": 125},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.96,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=2,
            word_id=5,
            text="ST",
            bounding_box={"x": 230, "y": 100, "width": 30, "height": 25},
            top_left={"x": 230, "y": 100},
            top_right={"x": 260, "y": 100},
            bottom_left={"x": 230, "y": 125},
            bottom_right={"x": 260, "y": 125},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.94,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=3,
            word_id=6,
            text="01/15/2024",
            bounding_box={"x": 100, "y": 150, "width": 120, "height": 25},
            top_left={"x": 100, "y": 150},
            top_right={"x": 220, "y": 150},
            bottom_left={"x": 100, "y": 175},
            bottom_right={"x": 220, "y": 175},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.97,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=4,
            word_id=7,
            text="COFFEE",
            bounding_box={"x": 100, "y": 200, "width": 80, "height": 25},
            top_left={"x": 100, "y": 200},
            top_right={"x": 180, "y": 200},
            bottom_left={"x": 100, "y": 225},
            bottom_right={"x": 180, "y": 225},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.96,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=4,
            word_id=8,
            text="$4.99",
            bounding_box={"x": 350, "y": 200, "width": 60, "height": 25},
            top_left={"x": 350, "y": 200},
            top_right={"x": 410, "y": 200},
            bottom_left={"x": 350, "y": 225},
            bottom_right={"x": 410, "y": 225},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.98,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=5,
            word_id=9,
            text="TOTAL",
            bounding_box={"x": 100, "y": 300, "width": 70, "height": 25},
            top_left={"x": 100, "y": 300},
            top_right={"x": 170, "y": 300},
            bottom_left={"x": 100, "y": 325},
            bottom_right={"x": 170, "y": 325},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.97,
        ),
        ReceiptWord(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=5,
            word_id=10,
            text="$4.99",
            bounding_box={"x": 350, "y": 300, "width": 60, "height": 25},
            top_left={"x": 350, "y": 300},
            top_right={"x": 410, "y": 300},
            bottom_left={"x": 350, "y": 325},
            bottom_right={"x": 410, "y": 325},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.99,
        ),
    ]

    lines = [
        ReceiptLine(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=1,
            text=f"{merchant_name} SUPERCENTER",
            words=[1, 2],
            bounding_box={"x": 100, "y": 50, "width": 360, "height": 30},
        ),
        ReceiptLine(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=2,
            text="123 MAIN ST",
            words=[3, 4, 5],
            bounding_box={"x": 100, "y": 100, "width": 160, "height": 25},
        ),
        ReceiptLine(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=3,
            text="01/15/2024",
            words=[6],
            bounding_box={"x": 100, "y": 150, "width": 120, "height": 25},
        ),
        ReceiptLine(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=4,
            text="COFFEE $4.99",
            words=[7, 8],
            bounding_box={"x": 100, "y": 200, "width": 310, "height": 25},
        ),
        ReceiptLine(
            receipt_id=int(receipt_id.split("_")[1]),
            image_id=f"img_{receipt_id}",
            line_id=5,
            text="TOTAL $4.99",
            words=[9, 10],
            bounding_box={"x": 100, "y": 300, "width": 310, "height": 25},
        ),
    ]

    metadata = ReceiptMetadata(
        receipt_id=int(receipt_id.split("_")[1]),
        image_id=f"img_{receipt_id}",
        merchant_name=merchant_name,
        canonical_merchant_name=merchant_name.title(),
        merchant_category="Retail",
        place_id="ChIJrTLr-GyuEmsRBfy61i59si0",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    return words, lines, metadata


def create_complex_receipt() -> tuple[List[ReceiptWord], List[ReceiptLine]]:
    """Create a more complex receipt with various patterns."""
    # This would include:
    # - Multiple products with quantities
    # - Tax calculations
    # - Phone numbers and websites
    # - Various date formats
    # - Discount amounts
    # Implementation left as an exercise
    pass
