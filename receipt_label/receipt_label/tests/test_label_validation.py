import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Set environment variables before any imports
os.environ.update(
    {
        "DYNAMO_TABLE_NAME": "test-table",
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

# Test merchant names
TEST_MERCHANT = "STARBUCKS"
TEST_MERCHANT_CANONICAL = "Starbucks"


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
        match = SimpleNamespace(id="m1", score=self.query_score, metadata={})
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


def _make_label(
    label: str, image_id="img_001", receipt_id=1, line_id=1, word_id=1
) -> SimpleNamespace:
    """Helper function to create labels with custom IDs."""
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label=label,
    )


@pytest.mark.unit
def test_validate_address(mocker):
    # Import here after mocks are set up
    from receipt_label.label_validation.validate_address import (
        validate_address,
    )

    fake_index = FakePineconeIndex()
    mocker.patch(
        "receipt_label.label_validation.validate_address.pinecone_index",
        fake_index,
    )

    word = SimpleNamespace(text="123 Main St")
    label = _make_label("ADDRESS")
    metadata = SimpleNamespace(canonical_address="123 main st, city")

    # Test valid address
    result = validate_address(word, label, metadata)
    assert result.status == "VALIDATED"
    assert result.is_consistent
    assert result.avg_similarity == 1.0
    assert result.image_id == label.image_id
    assert result.receipt_id == label.receipt_id
    assert result.line_id == label.line_id
    assert result.word_id == label.word_id
    assert result.label == label.label
    assert isinstance(result.neighbors, list)
    assert result.pinecone_id

    # Test invalid address
    bad_meta = SimpleNamespace(canonical_address="456 other ave")
    result = validate_address(word, label, bad_meta)
    assert result.status == "VALIDATED"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
def test_validate_address_no_vector(mocker):
    """Test address validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_address import (
        validate_address,
    )

    fake_index = FakePineconeIndex(has_vector=False)
    mocker.patch(
        "receipt_label.label_validation.validate_address.pinecone_index",
        fake_index,
    )

    word = SimpleNamespace(text="123 Main St")
    label = _make_label("ADDRESS")
    metadata = SimpleNamespace(canonical_address="123 main st, city")

    result = validate_address(word, label, metadata)
    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected_consistent",
    [
        ("$10.00", True),
        ("$1,234.56", True),
        ("10.00", True),
        ("hello", False),
        ("not_currency", False),
    ],
)
def test_validate_currency(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_currency import (
        validate_currency,
    )

    fake_index = FakePineconeIndex(query_score=HIGH_QUERY_SCORE)
    mocker.patch(
        "receipt_label.label_validation.validate_currency.pinecone_index",
        fake_index,
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
    """Test currency validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_currency import (
        validate_currency,
    )

    fake_index = FakePineconeIndex(has_vector=False)
    mocker.patch(
        "receipt_label.label_validation.validate_currency.pinecone_index",
        fake_index,
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
    ],
)
def test_validate_phone_number(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_phone_number import (
        validate_phone_number,
    )

    fake_index = FakePineconeIndex(query_score=MEDIUM_QUERY_SCORE)
    mocker.patch(
        "receipt_label.label_validation.validate_phone_number.pinecone_index",
        fake_index,
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
    """Test phone number validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_phone_number import (
        validate_phone_number,
    )

    fake_index = FakePineconeIndex(has_vector=False)
    mocker.patch(
        "receipt_label.label_validation.validate_phone_number.pinecone_index",
        fake_index,
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

    fake_index = FakePineconeIndex(query_score=DEFAULT_QUERY_SCORE)
    mocker.patch(
        "receipt_label.label_validation.validate_merchant_name.pinecone_index",
        fake_index,
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
    mocker.patch(
        "receipt_label.label_validation.validate_merchant_name.pinecone_index",
        fake_index,
    )

    word = SimpleNamespace(text=TEST_MERCHANT)
    label = _make_label("MERCHANT_NAME")

    # Test matching canonical name
    meta = SimpleNamespace(canonical_merchant_name=TEST_MERCHANT_CANONICAL)
    result = validate_merchant_name_google(word, label, meta)
    assert result.status == "VALIDATED"
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(1.0)
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
    mocker.patch(
        "receipt_label.label_validation.validate_merchant_name.pinecone_index",
        fake_index,
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
        ("2024-05-01", True),
        ("05/01/2024", True),
        ("01-May-2024", True),
        ("invalid", False),
        ("not-a-date", False),
    ],
)
def test_validate_date(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_date import validate_date

    fake_index = FakePineconeIndex(query_score=LOW_QUERY_SCORE)
    mocker.patch(
        "receipt_label.label_validation.validate_date.pinecone_index",
        fake_index,
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
    """Test date validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_date import validate_date

    fake_index = FakePineconeIndex(has_vector=False)
    mocker.patch(
        "receipt_label.label_validation.validate_date.pinecone_index",
        fake_index,
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
        ("12:30 PM", True),
        ("3:45 am", True),
        ("23:59", True),
        ("99:99", False),
        ("not-time", False),
    ],
)
def test_validate_time(mocker, text, expected_consistent):
    from receipt_label.label_validation.validate_time import validate_time

    fake_index = FakePineconeIndex(query_score=LOW_QUERY_SCORE)
    mocker.patch(
        "receipt_label.label_validation.validate_time.pinecone_index",
        fake_index,
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
    """Test time validation when no vector is found in Pinecone."""
    from receipt_label.label_validation.validate_time import validate_time

    fake_index = FakePineconeIndex(has_vector=False)
    mocker.patch(
        "receipt_label.label_validation.validate_time.pinecone_index",
        fake_index,
    )

    label_time = _make_label("TIME")
    word_time = SimpleNamespace(text="12:30 PM")
    result = validate_time(word_time, label_time)

    assert result.status == "NO_VECTOR"
    assert not result.is_consistent
    assert result.avg_similarity == 0.0
