import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Set environment variables before any imports
os.environ.update(
    {
        "DYNAMODB_TABLE_NAME": "test-table",
        "PINECONE_API_KEY": "test-key",
        "OPENAI_API_KEY": "test-key",
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test-host",
    }
)


# Mock Pinecone index for integration tests
class IntegrationFakePineconeIndex:
    """Fake Pinecone index that simulates more realistic behavior."""

    def __init__(self):
        # Store vectors to simulate persistence
        self.stored_vectors = {}
        self.query_count = 0
        self.fetch_count = 0

    def fetch(self, ids, namespace="words"):
        """Simulate fetching vectors with realistic behavior."""
        self.fetch_count += 1
        vectors = {}
        for id in ids:
            if id in self.stored_vectors:
                vectors[id] = self.stored_vectors[id]
            else:
                # Always return a mock vector to avoid NO_VECTOR status
                vectors[id] = SimpleNamespace(
                    id=id,
                    values=[0.1] * 10,  # Mock vector values
                    metadata={
                        "text": "test text",
                        "left": "left",
                        "right": "right",
                    },
                )
        return SimpleNamespace(vectors=vectors)

    def query(
        self,
        vector=None,
        top_k=10,
        include_metadata=True,
        filter=None,
        namespace="words",
    ):
        """Simulate querying with realistic behavior."""
        self.query_count += 1
        # Return consistently high scores to pass validation thresholds
        base_score = 0.90  # High enough for all validation thresholds
        matches = [
            SimpleNamespace(
                id=f"match_{i}", score=base_score - (i * 0.02), metadata={}
            )
            for i in range(min(3, top_k))
        ]
        return SimpleNamespace(matches=matches)

    def upsert(self, vectors, namespace="words"):
        """Simulate upserting vectors."""
        for vector in vectors:
            self.stored_vectors[vector.id] = SimpleNamespace(
                id=vector.id, values=vector.values, metadata=vector.metadata
            )


@pytest.fixture(autouse=True)
def mock_clients_integration(mocker):
    """Mock all client initializations for integration tests."""
    # Mock DynamoClient
    mock_dynamo = MagicMock()
    mocker.patch("receipt_dynamo.DynamoClient", return_value=mock_dynamo)

    # Mock OpenAI client
    mock_openai = MagicMock()
    mocker.patch("openai.OpenAI", return_value=mock_openai)

    # Mock Pinecone
    mock_pinecone_client = MagicMock()
    mock_pinecone_index = MagicMock()
    mock_pinecone_client.Index.return_value = mock_pinecone_index
    mocker.patch("pinecone.Pinecone", return_value=mock_pinecone_client)

    return mock_dynamo, mock_openai, mock_pinecone_index


@pytest.mark.integration
class TestValidationFunctionIntegration:
    """Test interactions between different validation functions."""

    def test_receipt_validation_workflow(self, mocker):
        """Test a complete receipt validation workflow."""
        # Import all validation functions
        from receipt_label.label_validation.validate_address import (
            validate_address,
        )
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )
        from receipt_label.label_validation.validate_date import validate_date
        from receipt_label.label_validation.validate_merchant_name import (
            validate_merchant_name_google,
            validate_merchant_name_pinecone,
        )
        from receipt_label.label_validation.validate_phone_number import (
            validate_phone_number,
        )
        from receipt_label.label_validation.validate_time import validate_time

        # Create a shared mock client manager
        mock_client_manager = MagicMock()
        mock_client_manager.pinecone = IntegrationFakePineconeIndex()

        # Mock get_client_manager for all validation modules
        mocker.patch(
            "receipt_label.label_validation.validate_address.get_client_manager",
            return_value=mock_client_manager,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_client_manager,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_date.get_client_manager",
            return_value=mock_client_manager,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=mock_client_manager,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_phone_number.get_client_manager",
            return_value=mock_client_manager,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_time.get_client_manager",
            return_value=mock_client_manager,
        )

        # Simulate a complete receipt with all fields
        receipt_data = {
            "merchant": SimpleNamespace(
                word=SimpleNamespace(text="STARBUCKS COFFEE"),
                label=SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=1,
                    word_id=1,
                    label="MERCHANT_NAME",
                ),
                canonical_name="Starbucks",
            ),
            "address": SimpleNamespace(
                word=SimpleNamespace(text="123 Main St., Suite 100"),
                label=SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=2,
                    word_id=5,
                    label="ADDRESS",
                ),
                metadata=SimpleNamespace(
                    canonical_address="123 main street suite 100"
                ),
            ),
            "phone": SimpleNamespace(
                word=SimpleNamespace(text="(555) 123-4567"),
                label=SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=3,
                    word_id=10,
                    label="PHONE_NUMBER",
                ),
            ),
            "date": SimpleNamespace(
                word=SimpleNamespace(text="2024-01-15"),
                label=SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=4,
                    word_id=15,
                    label="DATE",
                ),
            ),
            "time": SimpleNamespace(
                word=SimpleNamespace(text="2:30 PM"),
                label=SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=4,
                    word_id=16,
                    label="TIME",
                ),
            ),
            "total": SimpleNamespace(
                word=SimpleNamespace(text="$25.99"),
                label=SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=10,
                    word_id=30,
                    label="TOTAL",
                ),
            ),
        }

        # Validate all fields
        results = {}

        # Merchant validation
        results["merchant_pinecone"] = validate_merchant_name_pinecone(
            receipt_data["merchant"].word,
            receipt_data["merchant"].label,
            receipt_data["merchant"].canonical_name,
        )

        results["merchant_google"] = validate_merchant_name_google(
            receipt_data["merchant"].word,
            receipt_data["merchant"].label,
            SimpleNamespace(
                canonical_merchant_name=receipt_data["merchant"].canonical_name
            ),
        )

        # Address validation
        results["address"] = validate_address(
            receipt_data["address"].word,
            receipt_data["address"].label,
            receipt_data["address"].metadata,
        )

        # Phone validation
        results["phone"] = validate_phone_number(
            receipt_data["phone"].word, receipt_data["phone"].label
        )

        # Date validation
        results["date"] = validate_date(
            receipt_data["date"].word, receipt_data["date"].label
        )

        # Time validation
        results["time"] = validate_time(
            receipt_data["time"].word, receipt_data["time"].label
        )

        # Currency validation
        results["total"] = validate_currency(
            receipt_data["total"].word, receipt_data["total"].label
        )

        # Verify all validations completed
        assert len(results) == 7

        # Check that all results have the expected structure
        for key, result in results.items():
            assert hasattr(result, "status")
            assert hasattr(result, "is_consistent")
            assert hasattr(result, "avg_similarity")
            assert result.status == "VALIDATED"

        # Verify query count increases with each validation
        assert mock_client_manager.pinecone.query_count > 0

        # Verify consistency across related fields
        assert results["merchant_pinecone"].is_consistent
        assert results["address"].is_consistent
        assert results["phone"].is_consistent
        assert results["date"].is_consistent
        assert results["time"].is_consistent
        assert results["total"].is_consistent

    def test_validation_order_independence(self, mocker):
        """Test that validation order doesn't affect results."""
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )
        from receipt_label.label_validation.validate_date import validate_date

        # Create separate fake indexes
        fake_index1 = IntegrationFakePineconeIndex()
        fake_index2 = IntegrationFakePineconeIndex()

        # Test data
        currency_word = SimpleNamespace(text="$19.99")
        currency_label = SimpleNamespace(
            image_id="img_001",
            receipt_id=1,
            line_id=5,
            word_id=20,
            label="SUBTOTAL",
        )
        date_word = SimpleNamespace(text="2024-03-15")
        date_label = SimpleNamespace(
            image_id="img_001",
            receipt_id=1,
            line_id=2,
            word_id=8,
            label="DATE",
        )

        # Test order 1: Currency then Date
        mock_client_manager1 = MagicMock()
        mock_client_manager1.pinecone = fake_index1
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_client_manager1,
        )
        mock_client_manager1.pinecone = fake_index1
        mocker.patch(
            "receipt_label.label_validation.validate_date.get_client_manager",
            return_value=mock_client_manager1,
        )

        result1_currency = validate_currency(currency_word, currency_label)
        result1_date = validate_date(date_word, date_label)

        # Test order 2: Date then Currency
        mock_client_manager2 = MagicMock()
        mock_client_manager2.pinecone = fake_index2
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_client_manager2,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_date.get_client_manager",
            return_value=mock_client_manager2,
        )

        result2_date = validate_date(date_word, date_label)
        result2_currency = validate_currency(currency_word, currency_label)

        # Results should be consistent regardless of order
        assert result1_currency.is_consistent == result2_currency.is_consistent
        assert result1_date.is_consistent == result2_date.is_consistent

    def test_validation_with_mixed_quality_data(self, mocker):
        """Test validation with a mix of valid and invalid data."""
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )
        from receipt_label.label_validation.validate_date import validate_date
        from receipt_label.label_validation.validate_phone_number import (
            validate_phone_number,
        )

        fake_index = IntegrationFakePineconeIndex()
        # Currency mock setup
        mock_currency_manager = MagicMock()
        mock_currency_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_currency_manager,
        )
        # Phone mock setup
        mock_phone_manager = MagicMock()
        mock_phone_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_phone_number.get_client_manager",
            return_value=mock_phone_manager,
        )
        # Date mock setup
        mock_date_manager = MagicMock()
        mock_date_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_date.get_client_manager",
            return_value=mock_date_manager,
        )

        # Mix of valid and invalid data
        test_data = [
            # Valid entries
            (
                SimpleNamespace(text="$10.00"),
                SimpleNamespace(label="TOTAL"),
                validate_currency,
                True,
            ),
            (
                SimpleNamespace(text="555-123-4567"),
                SimpleNamespace(label="PHONE_NUMBER"),
                validate_phone_number,
                True,
            ),
            (
                SimpleNamespace(text="2024-01-01"),
                SimpleNamespace(label="DATE"),
                validate_date,
                True,
            ),
            # Invalid entries
            (
                SimpleNamespace(text="not money"),
                SimpleNamespace(label="TOTAL"),
                validate_currency,
                False,
            ),
            (
                SimpleNamespace(text="not a phone"),
                SimpleNamespace(label="PHONE_NUMBER"),
                validate_phone_number,
                False,
            ),
            (
                SimpleNamespace(text="not a date"),
                SimpleNamespace(label="DATE"),
                validate_date,
                False,
            ),
        ]

        results = []
        for word, label_type, validation_func, expected_valid in test_data:
            label = SimpleNamespace(
                image_id="img_001",
                receipt_id=1,
                line_id=1,
                word_id=1,
                label=label_type,
            )
            result = validation_func(word, label)
            results.append(result)
            assert result.is_consistent == expected_valid

        # Verify we processed all items
        assert len(results) == 6

        # Check that valid items have higher similarity scores than invalid ones
        valid_scores = [
            r.avg_similarity
            for r, (_, _, _, expected) in zip(results, test_data)
            if expected
        ]
        invalid_scores = [
            r.avg_similarity
            for r, (_, _, _, expected) in zip(results, test_data)
            if not expected
        ]

        if valid_scores and invalid_scores:
            assert max(invalid_scores) <= min(valid_scores)

    def test_validation_error_propagation(self, mocker):
        """Test how errors in one validation affect others."""
        from receipt_label.label_validation.validate_address import (
            validate_address,
        )
        from receipt_label.label_validation.validate_merchant_name import (
            validate_merchant_name_pinecone,
        )

        # Create a fake index that fails after first use
        class FailingIndex(IntegrationFakePineconeIndex):
            def __init__(self):
                super().__init__()
                self.call_count = 0

            def fetch(self, *args, **kwargs):
                self.call_count += 1
                if (
                    self.call_count > 2
                ):  # Allow first validation to complete (fetch + query)
                    raise ConnectionError("Simulated connection failure")
                return super().fetch(*args, **kwargs)

            def query(self, **kwargs):
                self.call_count += 1
                if (
                    self.call_count > 2
                ):  # Allow first validation to complete (fetch + query)
                    raise ConnectionError("Simulated connection failure")
                return super().query(**kwargs)

        fake_index = FailingIndex()
        # Address mock setup
        mock_address_manager = MagicMock()
        mock_address_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_address.get_client_manager",
            return_value=mock_address_manager,
        )
        # Merchant mock setup
        mock_merchant_manager = MagicMock()
        mock_merchant_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=mock_merchant_manager,
        )

        # First validation should succeed
        address_word = SimpleNamespace(text="123 Main St")
        address_label = SimpleNamespace(
            image_id="img_001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="ADDRESS",
        )
        address_meta = SimpleNamespace(canonical_address="123 main street")

        result1 = validate_address(address_word, address_label, address_meta)
        assert result1.status == "VALIDATED"

        # Second validation should fail
        merchant_word = SimpleNamespace(text="Starbucks")
        merchant_label = SimpleNamespace(
            image_id="img_001",
            receipt_id=1,
            line_id=2,
            word_id=5,
            label="MERCHANT_NAME",
        )

        with pytest.raises(ConnectionError):
            validate_merchant_name_pinecone(
                merchant_word, merchant_label, "Starbucks"
            )

    def test_batch_validation_performance(self, mocker):
        """Test performance characteristics of batch validation."""
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )

        fake_index = IntegrationFakePineconeIndex()
        # Currency mock setup
        mock_currency_manager = MagicMock()
        mock_currency_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_currency_manager,
        )

        # Simulate batch validation of multiple currency values
        currency_values = [
            "$10.00",
            "$25.50",
            "$100.00",
            "$5.99",
            "$1,234.56",
            "$0.99",
            "$50.00",
            "$75.25",
            "$200.00",
            "$15.00",
        ]

        results = []
        for i, amount in enumerate(currency_values):
            word = SimpleNamespace(text=amount)
            label = SimpleNamespace(
                image_id=f"img_{i:03d}",
                receipt_id=i,
                line_id=1,
                word_id=1,
                label="TOTAL",
            )
            result = validate_currency(word, label)
            results.append(result)

        # All valid currency should be consistent
        assert all(r.is_consistent for r in results)

        # Check that query count matches number of validations
        assert fake_index.query_count == len(currency_values)

        # Verify decreasing similarity scores (simulated by our fake index)
        scores = [r.avg_similarity for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_cross_validation_consistency(self, mocker):
        """Test consistency between related validation types."""
        from receipt_label.label_validation.validate_date import validate_date
        from receipt_label.label_validation.validate_time import validate_time

        fake_index = IntegrationFakePineconeIndex()
        # Date mock setup
        mock_date_manager = MagicMock()
        mock_date_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_date.get_client_manager",
            return_value=mock_date_manager,
        )
        # Time mock setup
        mock_time_manager = MagicMock()
        mock_time_manager.pinecone = fake_index
        mocker.patch(
            "receipt_label.label_validation.validate_time.get_client_manager",
            return_value=mock_time_manager,
        )

        # Test datetime components that should be consistent
        test_cases = [
            # Same receipt, consecutive words
            {
                "date": SimpleNamespace(text="2024-01-15"),
                "time": SimpleNamespace(text="14:30:00"),
                "date_label": SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=1,
                    word_id=1,
                    label="DATE",
                ),
                "time_label": SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=1,
                    word_id=2,
                    label="TIME",
                ),
            },
            # Different format but same receipt
            {
                "date": SimpleNamespace(text="01/15/2024"),
                "time": SimpleNamespace(text="2:30 PM"),
                "date_label": SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=2,
                    word_id=5,
                    label="DATE",
                ),
                "time_label": SimpleNamespace(
                    image_id="img_001",
                    receipt_id=1,
                    line_id=2,
                    word_id=6,
                    label="TIME",
                ),
            },
        ]

        for test_case in test_cases:
            date_result = validate_date(
                test_case["date"], test_case["date_label"]
            )
            time_result = validate_time(
                test_case["time"], test_case["time_label"]
            )

            # Both should be valid for valid datetime pairs
            assert date_result.is_consistent
            assert time_result.is_consistent

            # Both should have the same validation status
            assert date_result.status == time_result.status == "VALIDATED"

    def test_validation_state_isolation(self, mocker):
        """Test that validations don't affect each other's state."""
        from receipt_label.label_validation.validate_merchant_name import (
            validate_merchant_name_pinecone,
        )

        # Create separate indexes for each test
        fake_index1 = IntegrationFakePineconeIndex()
        fake_index2 = IntegrationFakePineconeIndex()

        # Test data
        merchant1 = SimpleNamespace(text="Walmart")
        merchant2 = SimpleNamespace(text="Target")
        label1 = SimpleNamespace(
            image_id="img_001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="MERCHANT_NAME",
        )
        label2 = SimpleNamespace(
            image_id="img_002",
            receipt_id=2,
            line_id=1,
            word_id=1,
            label="MERCHANT_NAME",
        )

        # Validate with first index
        mock_merchant_manager1 = MagicMock()
        mock_merchant_manager1.pinecone = fake_index1
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=mock_merchant_manager1,
        )
        result1 = validate_merchant_name_pinecone(merchant1, label1, "Walmart")

        # Validate with second index
        mock_merchant_manager2 = MagicMock()
        mock_merchant_manager2.pinecone = fake_index2
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=mock_merchant_manager2,
        )
        result2 = validate_merchant_name_pinecone(merchant2, label2, "Target")

        # Results should be independent
        assert result1.image_id == "img_001"
        assert result2.image_id == "img_002"
        assert result1.receipt_id == 1
        assert result2.receipt_id == 2

        # Query counts should be independent
        assert fake_index1.query_count == 1
        assert fake_index2.query_count == 1
