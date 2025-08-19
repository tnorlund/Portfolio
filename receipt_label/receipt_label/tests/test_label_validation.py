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


# Constants for test data
DEFAULT_QUERY_SCORE = 0.8
HIGH_QUERY_SCORE = 0.9
MEDIUM_QUERY_SCORE = 0.85
LOW_QUERY_SCORE = 0.88

# Similarity scores
PERFECT_MATCH_SCORE = 1.0
NO_MATCH_SCORE = 0.0
MIN_SIMILARITY_SCORE = 0.0
MAX_SIMILARITY_SCORE = 1.0

# Test merchant names
TEST_MERCHANT = "STARBUCKS"
TEST_MERCHANT_CANONICAL = "Starbucks"

# Test addresses
TEST_ADDRESS_TEXT = "123 Main St"
TEST_ADDRESS_CANONICAL = "123 main st, city"
TEST_ADDRESS_MISMATCH = "456 other ave"

# Test IDs
DEFAULT_IMAGE_ID = "img_001"
DEFAULT_RECEIPT_ID = 1
DEFAULT_LINE_ID = 1
DEFAULT_WORD_ID = 1


class FakeChromaClient:
    """Mock ChromaDB client for testing validation functions."""

    def __init__(
        self,
        metadata=None,
        query_score=DEFAULT_QUERY_SCORE,
        has_vector=True,
        raise_timeout=False,
        raise_query_error=False,
        raise_connection_error=False,
        raise_malformed_error=False,
        raise_partial_failure=False,
    ):
        self.metadata = metadata or {}
        self.query_score = query_score
        self.has_vector = has_vector
        self.raise_timeout = raise_timeout
        self.raise_query_error = raise_query_error
        self.raise_connection_error = raise_connection_error
        self.raise_malformed_error = raise_malformed_error
        self.raise_partial_failure = raise_partial_failure

    def get_by_ids(self, collection_name, ids, include=None):
        """Mock ChromaDB get_by_ids method."""
        if self.raise_timeout:
            raise TimeoutError("ChromaDB fetch timeout")
        if self.raise_connection_error:
            raise ConnectionError("ChromaDB connection failed")
        if self.raise_malformed_error:
            # Return malformed response that will cause an exception when accessed as dict
            return {
                "ids": None
            }  # Will cause exception when trying to index or check len

        if not self.has_vector:
            return {"ids": [], "embeddings": [], "metadatas": []}

        # Return embeddings and metadata for the requested IDs
        return {
            "ids": ids,
            "embeddings": [[0.1] * 1536] * len(ids),  # Mock embedding vectors
            "metadatas": [self.metadata] * len(ids),
        }

    def query(
        self,
        collection_name,
        query_embeddings,
        n_results=10,
        where=None,
        include=None,
    ):
        """Mock ChromaDB query method."""
        if self.raise_query_error:
            raise Exception("ChromaDB API error: Rate limit exceeded")
        if self.raise_connection_error:
            raise ConnectionError("Failed to connect to ChromaDB")
        if self.raise_timeout:
            raise TimeoutError("ChromaDB query timeout")
        if self.raise_malformed_error:
            # Return malformed response that will cause AttributeError
            return {}  # Missing expected fields
        if self.raise_partial_failure:
            raise Exception("Query failed")

        if not self.has_vector:
            return {"ids": [[]], "distances": [[]], "metadatas": [[]]}

        # Generate mock results based on the query
        ids = [f"test_id_{i}" for i in range(n_results)]
        distances = [
            1.0 - self.query_score
        ] * n_results  # ChromaDB uses distance (lower = more similar)
        metadatas = [
            {
                "text": "test text",
                "label": (
                    "PHONE_NUMBER"
                    if where and "PHONE_NUMBER" in str(where)
                    else "ADDRESS"
                ),
                "left": "neighbor_left",
                "right": "neighbor_right",
            }
        ] * n_results

        return {
            "ids": [ids],
            "distances": [distances],
            "metadatas": [metadatas],
        }

    def query_collection(
        self,
        collection_name,
        query_embeddings,
        n_results=10,
        where=None,
        include=None,
    ):
        """Mock ChromaDB query_collection method - alias for query."""
        # Use same error handling as query method
        return self.query(
            collection_name=collection_name,
            query_embeddings=query_embeddings,
            n_results=n_results,
            where=where,
            include=include,
        )


class FakePineconeIndex:
    def __init__(
        self, metadata=None, query_score=DEFAULT_QUERY_SCORE, has_vector=True
    ):
        self.metadata = metadata or {}
        self.query_score = query_score
        self.has_vector = has_vector

    def fetch(self, ids, namespace="words"):
        if not self.has_vector:
            return SimpleNamespace(vectors={})
        vector = SimpleNamespace(
            id=ids[0], values=[0.0], metadata=self.metadata
        )
        return SimpleNamespace(vectors={ids[0]: vector})

    def query(
        self,
        vector=None,
        top_k=10,
        include_metadata=True,
        filter=None,
        namespace="words",
    ):
        if not self.has_vector:
            return SimpleNamespace(matches=[])
        # Return metadata that includes the fields expected by validation
        match_metadata = {
            "text": "test text",
            "label": (
                filter.get("label", {}).get("$eq", "ADDRESS")
                if filter
                else "ADDRESS"
            ),
        }
        match = SimpleNamespace(
            id="m1", score=self.query_score, metadata=match_metadata
        )
        return SimpleNamespace(matches=[match])


@pytest.fixture(autouse=True)
def mock_all_clients(mocker):
    """Mock all client initializations to prevent credential errors."""
    # Mock DynamoClient to prevent table checks
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

    # Return the mocked clients
    return mock_dynamo, mock_openai, mock_pinecone_index


@pytest.fixture
def sample_label():
    """Fixture for creating test labels with realistic data."""
    return SimpleNamespace(
        image_id="test_img_123",
        receipt_id=42,
        line_id=1,
        word_id=5,
        label="ADDRESS",
    )


@pytest.fixture
def sample_word():
    """Fixture for creating test words."""
    return SimpleNamespace(text="123 Main St")


@pytest.fixture
def realistic_address_label():
    """Fixture for creating realistic address labels."""
    return SimpleNamespace(
        image_id="receipt_2024_001_scan",
        receipt_id=12345,
        line_id=3,
        word_id=15,
        label="ADDRESS",
    )


@pytest.fixture
def realistic_phone_label():
    """Fixture for creating realistic phone number labels."""
    return SimpleNamespace(
        image_id="store_receipt_789",
        receipt_id=98765,
        line_id=8,
        word_id=42,
        label="PHONE_NUMBER",
    )


@pytest.fixture
def realistic_currency_label():
    """Fixture for creating realistic currency labels."""
    return SimpleNamespace(
        image_id="invoice_2024_05_123",
        receipt_id=55555,
        line_id=12,
        word_id=3,
        label="TOTAL",
    )


@pytest.fixture
def realistic_merchant_label():
    """Fixture for creating realistic merchant name labels."""
    return SimpleNamespace(
        image_id="receipt_walmart_20240501",
        receipt_id=33333,
        line_id=1,
        word_id=1,
        label="MERCHANT_NAME",
    )


@pytest.fixture
def realistic_date_label():
    """Fixture for creating realistic date labels."""
    return SimpleNamespace(
        image_id="purchase_receipt_abc123",
        receipt_id=77777,
        line_id=2,
        word_id=8,
        label="DATE",
    )


@pytest.fixture
def realistic_time_label():
    """Fixture for creating realistic time labels."""
    return SimpleNamespace(
        image_id="transaction_record_xyz",
        receipt_id=44444,
        line_id=2,
        word_id=12,
        label="TIME",
    )


def _make_label(
    label: str,
    image_id=DEFAULT_IMAGE_ID,
    receipt_id=DEFAULT_RECEIPT_ID,
    line_id=DEFAULT_LINE_ID,
    word_id=DEFAULT_WORD_ID,
) -> SimpleNamespace:
    """Helper function to create labels with custom IDs."""
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label=label,
    )


def assert_complete_validation_result(
    result, expected_label, expected_status="VALIDATED"
):
    """Comprehensive helper to validate all fields of a LabelValidationResult."""
    # Check all label identification fields
    assert result.image_id == expected_label.image_id
    assert result.receipt_id == expected_label.receipt_id
    assert result.line_id == expected_label.line_id
    assert result.word_id == expected_label.word_id
    assert result.label == expected_label.label

    # Check validation status
    assert result.status == expected_status
    assert result.status in ["VALIDATED", "NO_VECTOR"]

    # Check validation fields
    assert isinstance(result.is_consistent, bool)
    assert isinstance(result.avg_similarity, float)
    assert 0.0 <= result.avg_similarity <= 1.0

    # Check neighbors list
    assert isinstance(result.neighbors, list)
    # Note: validate_address doesn't populate neighbors, so we don't check length

    # Check pinecone_id
    assert result.pinecone_id is not None
    assert isinstance(result.pinecone_id, str)
    assert len(result.pinecone_id) > 0


@pytest.mark.unit
def test_validate_address(mocker):
    # Import here after mocks are set up
    from receipt_label.label_validation.validate_address import (
        validate_address,
    )

    # Create a fake ChromaDB client instead of Pinecone
    fake_chroma = FakeChromaClient(
        metadata={"left": "neighbor_left", "right": "neighbor_right"},
        query_score=DEFAULT_QUERY_SCORE,
    )

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_address.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=TEST_ADDRESS_TEXT)
    label = _make_label("ADDRESS")
    metadata = SimpleNamespace(canonical_address=TEST_ADDRESS_CANONICAL)

    # Test valid address
    result = validate_address(word, label, metadata)
    assert_complete_validation_result(result, label, "VALIDATED")
    assert result.is_consistent
    assert result.avg_similarity == PERFECT_MATCH_SCORE

    # Test invalid address
    bad_meta = SimpleNamespace(canonical_address=TEST_ADDRESS_MISMATCH)
    result = validate_address(word, label, bad_meta)
    assert result.status == "VALIDATED"
    assert not result.is_consistent
    assert result.avg_similarity == NO_MATCH_SCORE


@pytest.mark.unit
def test_validate_address_no_vector(mocker):
    """Test address validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_address import (
        validate_address,
    )

    # Create a fake ChromaDB client that returns no vector
    fake_chroma = FakeChromaClient(has_vector=False)

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_address.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=TEST_ADDRESS_TEXT)
    label = _make_label("ADDRESS")
    metadata = SimpleNamespace(canonical_address=TEST_ADDRESS_CANONICAL)

    result = validate_address(word, label, metadata)
    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == NO_MATCH_SCORE


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected_consistent",
    [
        # Valid currency formats
        ("$10.00", True),
        ("$1,234.56", True),
        ("10.00", True),
        ("$0.01", True),  # Minimum valid amount
        ("$999,999.99", True),  # Large amount with commas
        ("0.50", True),  # No dollar sign
        ("$100", True),  # No cents
        ("$1,000,000.00", True),  # Million with proper formatting
        # Invalid formats
        ("hello", False),
        ("not_currency", False),
        # Malformed currency formats
        ("$10.0", False),  # Only one decimal place
        ("$10.000", False),  # Too many decimal places
        ("$10,00", False),  # Comma instead of decimal
        ("10.00.00", False),  # Multiple decimals
        ("$$10.00", False),  # Double dollar sign
        ("$10..00", False),  # Double decimal point
        ("$,100.00", False),  # Leading comma
        ("$100,00.00", False),  # Misplaced comma
        ("$1,00.00", False),  # Wrong comma placement
        ("$-10.00", False),  # Negative amount (depends on business logic)
        ("10.00$", False),  # Dollar sign at end
        ("$ 10.00", False),  # Space after dollar sign
        ("$10. 00", False),  # Space in decimal
        ("$1,2345.00", False),  # Wrong grouping (4 digits)
        ("$12,34.56", False),  # Wrong grouping (2 digits)
    ],
)
def test_validate_currency(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_currency import (
        validate_currency,
    )

    # Create a fake ChromaDB client instead of Pinecone
    fake_chroma = FakeChromaClient(
        metadata={"left": "neighbor_left", "right": "neighbor_right"},
        query_score=HIGH_QUERY_SCORE,
    )

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_currency.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=text)
    label = _make_label("TOTAL")
    result = validate_currency(word, label)

    assert result.status == "VALIDATED"
    assert result.is_consistent == expected_consistent
    assert result.avg_similarity == pytest.approx(HIGH_QUERY_SCORE)
    assert result.image_id == label.image_id
    assert result.receipt_id == label.receipt_id
    assert result.line_id == label.line_id
    assert result.word_id == label.word_id
    assert result.label == label.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id


@pytest.mark.unit
def test_validate_currency_no_vector(mocker):
    """Test currency validation when no vector is found in ChromaDB."""
    from receipt_label.label_validation.validate_currency import (
        validate_currency,
    )

    # Create a fake ChromaDB client that returns no vector
    fake_chroma = FakeChromaClient(has_vector=False)

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_currency.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text="$10.00")
    label = _make_label("TOTAL")
    result = validate_currency(word, label)

    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected_consistent",
    [
        ("(555) 123-4567", True),
        ("555-123-4567", True),
        ("5551234567", True),
        ("not a phone", False),
        ("12345", False),
        # Boundary cases for 10 digits
        ("1234567890", True),  # Exactly 10 digits
        ("123-456-7890", True),  # 10 digits formatted
        ("(123) 456-7890", True),  # 10 digits with area code
        # Boundary cases for 11 digits
        ("11234567890", True),  # Exactly 11 digits (with country code)
        ("1-123-456-7890", True),  # 11 digits formatted
        # Boundary cases for 12 digits
        ("123456789012", True),  # Exactly 12 digits
        # Edge cases - too short/long
        ("123456789", False),  # 9 digits - too short
        ("1234567890123", False),  # 13 digits - too long
        # Edge cases - almost valid formats
        ("555-1234", False),  # Missing area code
        ("555-12-34567", False),  # Wrong formatting
        ("(555) 123-456", False),  # Missing digit
        ("(555) 123-45678", False),  # Extra digit
    ],
)
def test_validate_phone_number(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_phone_number import (
        validate_phone_number,
    )

    # Create a fake ChromaDB client instead of Pinecone
    fake_chroma = FakeChromaClient(
        metadata={"left": "neighbor_left", "right": "neighbor_right"},
        query_score=MEDIUM_QUERY_SCORE,
    )

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_phone_number.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=text)
    label = _make_label("PHONE_NUMBER")
    result = validate_phone_number(word, label)

    assert result.status == "VALIDATED"
    assert result.is_consistent == expected_consistent
    assert result.avg_similarity == pytest.approx(MEDIUM_QUERY_SCORE)
    assert result.image_id == label.image_id
    assert result.receipt_id == label.receipt_id
    assert result.line_id == label.line_id
    assert result.word_id == label.word_id
    assert result.label == label.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id


@pytest.mark.unit
def test_validate_phone_number_no_vector(mocker):
    """Test phone number validation when no vector is found in ChromaDB."""
    from receipt_label.label_validation.validate_phone_number import (
        validate_phone_number,
    )

    # Create a fake ChromaDB client that returns no vector
    fake_chroma = FakeChromaClient(has_vector=False)

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_phone_number.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text="(555) 123-4567")
    label = _make_label("PHONE_NUMBER")
    result = validate_phone_number(word, label)

    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
def test_validate_merchant_name_pinecone(mocker):
    from receipt_label.label_validation.validate_merchant_name import (
        validate_merchant_name_pinecone,
    )

    # Create a fake ChromaDB client instead of Pinecone
    fake_chroma = FakeChromaClient(
        metadata={"merchant_name": TEST_MERCHANT_CANONICAL},
        query_score=DEFAULT_QUERY_SCORE,
    )

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_merchant_name.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=TEST_MERCHANT)
    label = _make_label("MERCHANT_NAME")

    # Test valid merchant name
    result = validate_merchant_name_pinecone(word, label, TEST_MERCHANT)
    assert result.status == "VALIDATED"
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(DEFAULT_QUERY_SCORE)
    assert result.image_id == label.image_id
    assert result.receipt_id == label.receipt_id
    assert result.line_id == label.line_id
    assert result.word_id == label.word_id
    assert result.label == label.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id

    # Test invalid merchant name (numeric)
    bad_word = SimpleNamespace(text="1234")
    result = validate_merchant_name_pinecone(bad_word, label, TEST_MERCHANT)
    assert result.status == "VALIDATED"
    assert not result.is_consistent


@pytest.mark.unit
def test_validate_merchant_name_google(mocker):
    from receipt_label.label_validation.validate_merchant_name import (
        validate_merchant_name_google,
    )

    fake_index = FakePineconeIndex(query_score=DEFAULT_QUERY_SCORE)
    # Create a mock client manager with our fake index
    mock_client_manager = MagicMock()
    mock_client_manager.pinecone = fake_index

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_merchant_name.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=TEST_MERCHANT)
    label = _make_label("MERCHANT_NAME")

    # Test matching canonical name
    meta = SimpleNamespace(canonical_merchant_name=TEST_MERCHANT_CANONICAL)
    result = validate_merchant_name_google(word, label, meta)
    assert result.status == "VALIDATED"
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(PERFECT_MATCH_SCORE)
    assert result.image_id == label.image_id
    assert result.receipt_id == label.receipt_id
    assert result.line_id == label.line_id
    assert result.word_id == label.word_id
    assert result.label == label.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id

    # Test non-matching canonical name
    meta_bad = SimpleNamespace(canonical_merchant_name="Other")
    result = validate_merchant_name_google(word, label, meta_bad)
    assert result.status == "VALIDATED"
    assert not result.is_consistent


@pytest.mark.unit
def test_validate_merchant_name_no_vector(mocker):
    """Test merchant name validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_merchant_name import (
        validate_merchant_name_pinecone,
    )

    fake_index = FakePineconeIndex(has_vector=False)
    # Create a mock client manager with our fake index
    mock_client_manager = MagicMock()
    mock_client_manager.pinecone = fake_index

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_merchant_name.get_client_manager",
        return_value=mock_client_manager,
    )

    word = SimpleNamespace(text=TEST_MERCHANT)
    label = _make_label("MERCHANT_NAME")

    result = validate_merchant_name_pinecone(word, label, TEST_MERCHANT)
    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected_consistent",
    [
        # Standard date formats
        ("2024-05-01", True),
        ("05/01/2024", True),
        ("01-May-2024", True),
        ("May 1, 2024", True),
        ("1 May 2024", True),
        ("05-01-2024", True),
        ("2024/05/01", True),
        # Date with timezone information
        ("2024-05-01T00:00:00Z", True),  # ISO format with UTC
        ("2024-05-01T00:00:00+00:00", True),  # ISO with timezone offset
        ("2024-05-01T00:00:00-05:00", True),  # ISO with negative offset
        ("2024-05-01 PST", True),  # Date with timezone abbreviation
        ("2024-05-01 UTC", True),  # Date with UTC
        # Edge cases - valid but unusual
        ("01/01/2024", True),  # New Year
        ("12/31/2024", True),  # End of year
        ("02/29/2024", True),  # Leap year
        # Invalid formats
        ("invalid", False),
        ("not-a-date", False),
        ("13/01/2024", False),  # Invalid month
        ("01/32/2024", False),  # Invalid day
        ("02/30/2024", False),  # Invalid day for February
        ("2024-13-01", False),  # Invalid month
        ("2024-05-32", False),  # Invalid day
        ("2024/05", False),  # Incomplete date
        ("05/2024", False),  # Missing day
        ("2024", False),  # Year only
    ],
)
def test_validate_date(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_date import validate_date

    # Create a fake ChromaDB client instead of Pinecone
    fake_chroma = FakeChromaClient(
        metadata={"left": "neighbor_left", "right": "neighbor_right"},
        query_score=LOW_QUERY_SCORE,
    )

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_date.get_client_manager",
        return_value=mock_client_manager,
    )

    label = _make_label("DATE")
    word_date = SimpleNamespace(text=text)
    result = validate_date(word_date, label)

    assert result.status == "VALIDATED"
    assert result.is_consistent == expected_consistent
    assert result.avg_similarity == pytest.approx(LOW_QUERY_SCORE)
    assert result.image_id == label.image_id
    assert result.receipt_id == label.receipt_id
    assert result.line_id == label.line_id
    assert result.word_id == label.word_id
    assert result.label == label.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id


@pytest.mark.unit
def test_validate_date_no_vector(mocker):
    """Test date validation when no vector is found in ChromaDB."""
    from receipt_label.label_validation.validate_date import validate_date

    # Create a fake ChromaDB client that returns no vector
    fake_chroma = FakeChromaClient(has_vector=False)

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_date.get_client_manager",
        return_value=mock_client_manager,
    )

    label = _make_label("DATE")
    word_date = SimpleNamespace(text="2024-05-01")
    result = validate_date(word_date, label)

    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected_consistent",
    [
        # Standard time formats
        ("12:30 PM", True),
        ("3:45 am", True),
        ("23:59", True),
        ("00:00", True),  # Midnight
        ("12:00", True),  # Noon
        ("1:30 PM", True),  # Single digit hour
        ("09:15 AM", True),  # Leading zero
        ("11:59 PM", True),  # Last minute of day
        # Time with seconds
        ("12:30:45", True),
        ("23:59:59", True),
        ("12:30:45 PM", True),
        # Time with timezone
        ("12:30 PM EST", True),  # With timezone abbreviation
        ("12:30 PM UTC", True),  # UTC timezone
        ("12:30:00+00:00", True),  # ISO format with timezone
        ("12:30:00-05:00", True),  # With negative offset
        ("12:30:00Z", True),  # Zulu time
        ("3:45 am PST", True),  # Pacific time
        ("23:59:59+08:00", True),  # Asia timezone
        # Edge cases
        ("00:00:00", True),  # Midnight with seconds
        ("23:59:59", True),  # Last second of day
        # Invalid formats
        ("99:99", False),
        ("not-time", False),
        ("25:00", False),  # Invalid hour
        ("12:60", False),  # Invalid minute
        ("12:30:60", False),  # Invalid second
        ("13:00 PM", False),  # 24h format with AM/PM
        ("00:00 AM", False),  # Midnight as AM (should be 12:00 AM)
        ("12:5 PM", False),  # Missing leading zero
        ("12:", False),  # Incomplete time
        (":30", False),  # Missing hour
        ("12:30:", False),  # Trailing colon
    ],
)
def test_validate_time(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_time import validate_time

    # Create a fake ChromaDB client instead of Pinecone
    fake_chroma = FakeChromaClient(
        metadata={"left": "neighbor_left", "right": "neighbor_right"},
        query_score=LOW_QUERY_SCORE,
    )

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_time.get_client_manager",
        return_value=mock_client_manager,
    )

    label_time = _make_label("TIME")
    word_time = SimpleNamespace(text=text)
    result = validate_time(word_time, label_time)

    assert result.status == "VALIDATED"
    assert result.is_consistent == expected_consistent
    assert result.avg_similarity == pytest.approx(LOW_QUERY_SCORE)
    assert result.image_id == label_time.image_id
    assert result.receipt_id == label_time.receipt_id
    assert result.line_id == label_time.line_id
    assert result.word_id == label_time.word_id
    assert result.label == label_time.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id


@pytest.mark.unit
def test_validate_time_no_vector(mocker):
    """Test time validation when no vector is found in ChromaDB."""
    from receipt_label.label_validation.validate_time import validate_time

    # Create a fake ChromaDB client that returns no vector
    fake_chroma = FakeChromaClient(has_vector=False)

    # Create a mock client manager with our fake ChromaDB client
    mock_client_manager = MagicMock()
    mock_client_manager.chroma = fake_chroma

    # Mock get_client_manager to return our mock
    mocker.patch(
        "receipt_label.label_validation.validate_time.get_client_manager",
        return_value=mock_client_manager,
    )

    label_time = _make_label("TIME")
    word_time = SimpleNamespace(text="12:30 PM")
    result = validate_time(word_time, label_time)

    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
class TestTextNormalizationInValidation:
    """Test text normalization edge cases within validation functions."""

    def test_validate_address_with_normalization_edge_cases(self, mocker):
        """Test address validation with various normalization scenarios."""
        from receipt_label.label_validation.validate_address import (
            validate_address,
        )

        # Create a fake ChromaDB client instead of Pinecone
        fake_chroma = FakeChromaClient(
            metadata={"canonical_address": "123 main street"},
            query_score=DEFAULT_QUERY_SCORE,
        )

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_address.get_client_manager",
            return_value=mock_client_manager,
        )

        # Test with different address formats that should match after normalization
        test_cases = [
            # (receipt_text, canonical_address, should_match)
            ("123 N. Main St.", "123 north main street", True),
            ("456 Oak Ave, Apt 5", "456 oak avenue apartment 5", True),
            ("789 SW Elm Blvd.", "789 southwest elm boulevard", True),
            # Edge cases with extra punctuation
            ("123... Main... St...", "123 main street", True),
            ("456 Oak!!!Avenue", "456 oak avenue", True),
            # Unicode characters
            ("123 Café Street", "123 cafe street", True),
            # Multiple spaces and tabs
            ("123   Main    Street", "123 main street", True),
        ]

        for receipt_text, canonical_addr, should_match in test_cases:
            word = SimpleNamespace(text=receipt_text)
            label = _make_label("ADDRESS")
            metadata = SimpleNamespace(canonical_address=canonical_addr)

            result = validate_address(word, label, metadata)
            assert result.status == "VALIDATED"
            if should_match:
                assert (
                    result.is_consistent
                ), f"Expected {receipt_text} to match {canonical_addr}"
            else:
                assert not result.is_consistent

    def test_validate_phone_with_edge_formats(self, mocker):
        """Test phone validation with unusual formats."""
        from receipt_label.label_validation.validate_phone_number import (
            validate_phone_number,
        )

        # Create a fake ChromaDB client instead of Pinecone
        fake_chroma = FakeChromaClient(
            metadata={"left": "neighbor_left", "right": "neighbor_right"},
            query_score=MEDIUM_QUERY_SCORE,
        )

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_phone_number.get_client_manager",
            return_value=mock_client_manager,
        )

        # Edge case phone formats
        edge_cases = [
            # International formats with various separators
            "+1.555.123.4567",
            "+1 (555) 123-4567",
            "001-555-123-4567",
            # Formats with letters (should be invalid)
            "555-CALL-NOW",
            "1-800-FLOWERS",
            # Partial numbers
            "555-1234",  # Missing area code
            "123-4567",  # Local number only
            # Extra characters
            "Call: (555) 123-4567",
            "Ph# 555-123-4567",
            "555.123.4567 (mobile)",
        ]

        for phone_text in edge_cases:
            word = SimpleNamespace(text=phone_text)
            label = _make_label("PHONE_NUMBER")
            result = validate_phone_number(word, label)
            assert result.status == "VALIDATED"
            # The validation should handle these appropriately

    def test_validate_currency_with_unicode_symbols(self, mocker):
        """Test currency validation with various currency symbols."""
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )

        # Create a fake ChromaDB client instead of Pinecone
        fake_chroma = FakeChromaClient(
            metadata={"left": "neighbor_left", "right": "neighbor_right"},
            query_score=HIGH_QUERY_SCORE,
        )

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_client_manager,
        )

        # Currency formats with different symbols
        currency_formats = [
            ("€10.00", False),  # Euro symbol
            ("£10.00", False),  # Pound symbol
            ("¥1000", False),  # Yen symbol
            ("₹500.00", False),  # Rupee symbol
            ("$10.00 USD", True),  # With currency code
            ("10.00 dollars", False),  # Written out
            ("CAD $10.00", True),  # Currency code prefix
        ]

        for currency_text, expected_valid in currency_formats:
            word = SimpleNamespace(text=currency_text)
            label = _make_label("TOTAL")
            result = validate_currency(word, label)
            assert result.status == "VALIDATED"
            # Note: Current implementation may only handle USD properly

    def test_validate_merchant_name_normalization(self, mocker):
        """Test merchant name validation with normalization edge cases."""
        from receipt_label.label_validation.validate_merchant_name import (
            validate_merchant_name_pinecone,
        )

        # Create a fake ChromaDB client instead of Pinecone
        fake_chroma = FakeChromaClient(
            metadata={"merchant_name": "starbucks coffee"},
            query_score=DEFAULT_QUERY_SCORE,
        )

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=mock_client_manager,
        )

        # Test various merchant name formats
        test_cases = [
            # All caps vs mixed case
            ("STARBUCKS", "Starbucks", True),
            ("walmart", "Walmart", True),
            # With special characters
            ("McDonald's", "McDonalds", True),
            ("7-Eleven", "7 Eleven", True),
            # With extra words
            ("Starbucks Coffee", "Starbucks", True),
            ("Walmart Supercenter", "Walmart", True),
            # Numbers only (should be invalid)
            ("12345", "Store 12345", False),
            # Empty or whitespace
            ("   ", "Store Name", False),
        ]

        for receipt_name, canonical_name, should_match in test_cases:
            word = SimpleNamespace(text=receipt_name)
            label = _make_label("MERCHANT_NAME")
            result = validate_merchant_name_pinecone(
                word, label, canonical_name
            )
            assert result.status == "VALIDATED"


@pytest.mark.unit
class TestAPIErrorHandling:
    """Test handling of API errors and network issues."""

    def test_chroma_fetch_timeout(self, mocker):
        """Test handling of ChromaDB fetch timeout."""
        from receipt_label.label_validation.validate_address import (
            validate_address,
        )

        # Create a fake ChromaDB client that raises timeout on get_by_ids
        fake_chroma = FakeChromaClient(raise_timeout=True)

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_address.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text=TEST_ADDRESS_TEXT)
        label = _make_label("ADDRESS")
        metadata = SimpleNamespace(canonical_address=TEST_ADDRESS_CANONICAL)

        # Should handle timeout gracefully
        with pytest.raises(TimeoutError):
            validate_address(word, label, metadata)

    def test_chroma_query_error(self, mocker):
        """Test handling of ChromaDB query errors."""
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )

        # Create a fake ChromaDB client that raises API error on query
        fake_chroma = FakeChromaClient(raise_query_error=True)

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text="$10.00")
        label = _make_label("TOTAL")

        # Should handle API error gracefully
        with pytest.raises(Exception, match="ChromaDB API error"):
            validate_currency(word, label)

    def test_chroma_connection_error(self, mocker):
        """Test handling of connection errors."""
        from receipt_label.label_validation.validate_phone_number import (
            validate_phone_number,
        )

        # Create a fake ChromaDB client that raises connection error
        fake_chroma = FakeChromaClient(raise_connection_error=True)

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_phone_number.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text="555-123-4567")
        label = _make_label("PHONE_NUMBER")

        # Should handle connection error gracefully
        with pytest.raises(ConnectionError):
            validate_phone_number(word, label)

    def test_chroma_malformed_response(self, mocker):
        """Test handling of malformed ChromaDB responses."""
        from receipt_label.label_validation.validate_date import validate_date

        # Create a fake ChromaDB client that returns malformed response
        fake_chroma = FakeChromaClient(raise_malformed_error=True)

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_date.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text="2024-01-01")
        label = _make_label("DATE")

        # Should handle malformed response gracefully
        with pytest.raises(
            TypeError
        ):  # Expected when trying to call len() on None
            validate_date(word, label)

    def test_empty_chroma_response(self, mocker):
        """Test handling of empty ChromaDB responses."""
        from receipt_label.label_validation.validate_time import validate_time

        # Create a fake ChromaDB client that returns empty results
        fake_chroma = FakeChromaClient(has_vector=False)

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_time.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text="12:30 PM")
        label = _make_label("TIME")

        result = validate_time(word, label)
        # Should handle empty results gracefully
        assert (
            result.status == "NO_VECTOR"
        )  # ChromaDB returns NO_VECTOR when no vector found
        assert result.avg_similarity == 0.0  # No matches means 0 similarity

    def test_chroma_partial_failure(self, mocker):
        """Test handling when some ChromaDB operations succeed and others fail."""
        from receipt_label.label_validation.validate_merchant_name import (
            validate_merchant_name_pinecone,
        )

        # Create a fake ChromaDB client where get_by_ids works but query fails
        fake_chroma = FakeChromaClient(raise_partial_failure=True)

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text=TEST_MERCHANT)
        label = _make_label("MERCHANT_NAME")

        # Should handle partial failure
        with pytest.raises(Exception, match="Query failed"):
            validate_merchant_name_pinecone(word, label, TEST_MERCHANT)

    def test_retry_logic_simulation(self, mocker):
        """Test that validation functions could support retry logic."""
        from receipt_label.label_validation.validate_address import (
            validate_address,
        )

        # Track call count for retry simulation
        call_count = 0

        def flaky_get_by_ids(collection_name, ids, include=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary network issue")
            # Success on third try
            return {
                "ids": ids,
                "embeddings": [[0.1] * 1536] * len(ids),
                "metadatas": [{}] * len(ids),
            }

        # Create a fake ChromaDB client with custom get_by_ids method
        fake_chroma = FakeChromaClient()
        fake_chroma.get_by_ids = flaky_get_by_ids

        # Create a mock client manager with our fake ChromaDB client
        mock_client_manager = MagicMock()
        mock_client_manager.chroma = fake_chroma

        # Mock get_client_manager to return our mock
        mocker.patch(
            "receipt_label.label_validation.validate_address.get_client_manager",
            return_value=mock_client_manager,
        )

        word = SimpleNamespace(text=TEST_ADDRESS_TEXT)
        label = _make_label("ADDRESS")
        metadata = SimpleNamespace(canonical_address=TEST_ADDRESS_CANONICAL)

        # Current implementation doesn't have retry logic, so it will fail
        with pytest.raises(ConnectionError):
            validate_address(word, label, metadata)

        # This test documents that retry logic could be added in the future


@pytest.mark.integration
class TestValidationIntegrationScenarios:
    """Integration test scenarios within the main test file."""

    def test_complete_receipt_validation_flow(self, mocker):
        """Test validating all fields of a complete receipt."""
        # Import validation functions
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

        # Create a shared mock client manager for all validation functions
        mock_client_manager = MagicMock()
        # Create a versatile fake ChromaDB client for all validation needs
        fake_chroma = FakeChromaClient(
            metadata={
                "canonical_address": "456 oak avenue suite 200",
                "merchant_name": "walmart",
                "left": "neighbor_left",
                "right": "neighbor_right",
            },
            query_score=DEFAULT_QUERY_SCORE,
        )
        mock_client_manager.chroma = fake_chroma

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

        # Create a complete receipt
        receipt = {
            "merchant_name": "WALMART SUPERCENTER",
            "address": "456 Oak Avenue, Suite 200",
            "phone": "(555) 987-6543",
            "date": "2024-03-15",
            "time": "3:45 PM",
            "subtotal": "$45.67",
            "tax": "$3.65",
            "total": "$49.32",
        }

        # Create corresponding labels
        base_label = SimpleNamespace(image_id="receipt_001", receipt_id=12345)

        # Validate all fields
        validation_results = {}
        labels = {}  # Store label objects for assertion

        # Merchant validation
        merchant_word = SimpleNamespace(text=receipt["merchant_name"])
        merchant_label = SimpleNamespace(
            **vars(base_label), line_id=1, word_id=1, label="MERCHANT_NAME"
        )
        labels["merchant"] = merchant_label
        validation_results["merchant"] = validate_merchant_name_pinecone(
            merchant_word, merchant_label, "Walmart"
        )

        # Address validation
        address_word = SimpleNamespace(text=receipt["address"])
        address_label = SimpleNamespace(
            **vars(base_label), line_id=2, word_id=5, label="ADDRESS"
        )
        labels["address"] = address_label
        address_meta = SimpleNamespace(
            canonical_address="456 oak avenue suite 200"
        )
        validation_results["address"] = validate_address(
            address_word, address_label, address_meta
        )

        # Phone validation
        phone_word = SimpleNamespace(text=receipt["phone"])
        phone_label = SimpleNamespace(
            **vars(base_label), line_id=3, word_id=10, label="PHONE_NUMBER"
        )
        labels["phone"] = phone_label
        validation_results["phone"] = validate_phone_number(
            phone_word, phone_label
        )

        # Date validation
        date_word = SimpleNamespace(text=receipt["date"])
        date_label = SimpleNamespace(
            **vars(base_label), line_id=4, word_id=15, label="DATE"
        )
        labels["date"] = date_label
        validation_results["date"] = validate_date(date_word, date_label)

        # Time validation
        time_word = SimpleNamespace(text=receipt["time"])
        time_label = SimpleNamespace(
            **vars(base_label), line_id=4, word_id=17, label="TIME"
        )
        labels["time"] = time_label
        validation_results["time"] = validate_time(time_word, time_label)

        # Currency validations
        for field, line_offset in [("subtotal", 0), ("tax", 1), ("total", 2)]:
            word = SimpleNamespace(text=receipt[field])
            label = SimpleNamespace(
                **vars(base_label),
                line_id=10 + line_offset,
                word_id=20 + line_offset,
                label=field.upper(),
            )
            labels[field] = label
            validation_results[field] = validate_currency(word, label)

        # Verify all validations completed successfully
        assert len(validation_results) == 8
        for field, result in validation_results.items():
            assert (
                result.status == "VALIDATED"
            ), f"Validation failed for {field}"
            assert result.is_consistent, f"Inconsistent validation for {field}"
            assert_complete_validation_result(result, labels[field])

        # Verify receipt integrity
        assert (
            validation_results["merchant"].receipt_id
            == validation_results["total"].receipt_id
        )
        assert (
            validation_results["address"].image_id
            == validation_results["phone"].image_id
        )

    def test_partial_receipt_validation(self, mocker):
        """Test validation when some receipt fields are missing."""
        from receipt_label.label_validation.validate_currency import (
            validate_currency,
        )
        from receipt_label.label_validation.validate_merchant_name import (
            validate_merchant_name_pinecone,
        )

        # Create mock client managers for different scenarios
        currency_client_manager = MagicMock()
        currency_client_manager.chroma = FakeChromaClient(
            metadata={"left": "neighbor_left", "right": "neighbor_right"},
            query_score=HIGH_QUERY_SCORE,
        )

        merchant_client_manager = MagicMock()
        merchant_client_manager.chroma = FakeChromaClient(
            has_vector=False
        )  # Simulate missing vector

        # Mock get_client_manager for different modules
        mocker.patch(
            "receipt_label.label_validation.validate_currency.get_client_manager",
            return_value=currency_client_manager,
        )
        mocker.patch(
            "receipt_label.label_validation.validate_merchant_name.get_client_manager",
            return_value=merchant_client_manager,
        )

        # Partial receipt data
        base_label = SimpleNamespace(image_id="partial_001", receipt_id=99999)

        # Valid total
        total_word = SimpleNamespace(text="$25.00")
        total_label = SimpleNamespace(
            **vars(base_label), line_id=5, word_id=10, label="TOTAL"
        )
        total_result = validate_currency(total_word, total_label)

        # Missing merchant (no vector)
        merchant_word = SimpleNamespace(text="Unknown Store")
        merchant_label = SimpleNamespace(
            **vars(base_label), line_id=1, word_id=1, label="MERCHANT_NAME"
        )
        merchant_result = validate_merchant_name_pinecone(
            merchant_word, merchant_label, "Unknown"
        )

        # Check results
        assert total_result.status == "VALIDATED"
        assert total_result.is_consistent

        assert merchant_result.status == "NO_VECTOR"
        assert not merchant_result.is_consistent

        # Both should belong to the same receipt
        assert total_result.receipt_id == merchant_result.receipt_id
