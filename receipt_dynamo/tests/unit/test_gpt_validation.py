# test_gpt_validation.py
import pytest
from datetime import datetime
from receipt_dynamo.entities.gpt_validation import GPTValidation, itemToGPTValidation

# --- Fixtures ---


@pytest.fixture
def sample_gpt_validation():
    """
    Returns a sample GPTValidation instance with valid attributes.
    """
    return GPTValidation(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it is the total.",
        timestamp_added="2021-01-01T00:00:00",
    )


# --- Initialization Tests ---


@pytest.mark.unit
def test_gpt_validation_init_valid(sample_gpt_validation):
    """Test that GPTValidation initializes correct attributes."""
    gv = sample_gpt_validation
    assert gv.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert gv.receipt_id == 7
    assert gv.query == "Is this the total amount?"
    assert gv.response == "Yes, it is the total."
    assert gv.timestamp_added == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_gpt_validation_init_invalid_image_id():
    """Test that GPTValidation raises ValueError for an invalid image_id."""
    with pytest.raises(ValueError):
        GPTValidation(
            image_id=1,  # not a string
            receipt_id=7,
            query="Is this the total amount?",
            response="Yes, it is the total.",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError):
        GPTValidation(
            image_id="bad-uuid",
            receipt_id=7,
            query="Is this the total amount?",
            response="Yes, it is the total.",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_validation_init_invalid_receipt_id():
    """Test that GPTValidation raises ValueError for an invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id="7",  # wrong type
            query="Is this the total amount?",
            response="Yes, it is the total.",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=0,  # zero value
            query="Is this the total amount?",
            response="Yes, it is the total.",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_validation_init_invalid_query():
    """Test that GPTValidation raises ValueError for an invalid query."""
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="",  # empty string
            response="Yes, it is the total.",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query=123,  # wrong type
            response="Yes, it is the total.",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_validation_init_invalid_response():
    """Test that GPTValidation raises ValueError for an invalid response."""
    with pytest.raises(ValueError, match="response must be a non-empty string"):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="Is this the total amount?",
            response="",  # empty string
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="response must be a non-empty string"):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="Is this the total amount?",
            response=123,  # wrong type
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_validation_init_invalid_timestamp():
    """Test that GPTValidation raises ValueError for an invalid timestamp_added."""
    with pytest.raises(
        ValueError, match="timestamp_added must be a datetime object or a string"
    ):
        GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="Is this the total amount?",
            response="Yes, it is the total.",
            timestamp_added=1234567890,  # wrong type
        )


# --- Equality, Iteration, and Representation Tests ---


@pytest.mark.unit
def test_gpt_validation_eq(sample_gpt_validation):
    """Test __eq__ method for GPTValidation."""
    gv1 = sample_gpt_validation
    gv2 = GPTValidation(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it is the total.",
        timestamp_added="2021-01-01T00:00:00",
    )
    assert gv1 == gv2

    gv3 = GPTValidation(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Different query",
        response="Yes, it is the total.",
        timestamp_added="2021-01-01T00:00:00",
    )
    assert gv1 != gv3
    assert gv1 != "not a GPTValidation"


@pytest.mark.unit
def test_gpt_validation_iter(sample_gpt_validation):
    """Test that __iter__ yields the correct attribute key/value pairs."""
    as_dict = dict(sample_gpt_validation)
    assert as_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert as_dict["receipt_id"] == 7
    assert as_dict["query"] == "Is this the total amount?"
    assert as_dict["response"] == "Yes, it is the total."
    assert as_dict["timestamp_added"] == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_gpt_validation_repr(sample_gpt_validation):
    """Test that __repr__ includes relevant attribute data."""
    rep = repr(sample_gpt_validation)
    assert "image_id=" in rep
    assert "receipt_id=7" in rep
    assert "query=" in rep
    assert "response=" in rep
    assert "timestamp_added=" in rep


# --- Key Generation and Item Conversion Tests ---


@pytest.mark.unit
def test_gpt_validation_key(sample_gpt_validation):
    """Test that the primary key is generated as expected."""
    key = sample_gpt_validation.key()
    assert key["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert key["SK"]["S"] == "RECEIPT#00007#QUERY#VALIDATION"


@pytest.mark.unit
def test_gpt_validation_to_item(sample_gpt_validation):
    """Test that to_item returns a DynamoDB-ready dictionary."""
    item = sample_gpt_validation.to_item()
    assert "PK" in item
    assert "SK" in item
    assert item["TYPE"]["S"] == "GPT_VALIDATION"
    assert item["query"]["S"] == "Is this the total amount?"
    assert item["response"]["S"] == "Yes, it is the total."
    assert item["timestamp_added"]["S"] == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_item_to_gpt_validation(sample_gpt_validation):
    """Test that a DynamoDB item converts back to a GPTValidation object."""
    item = sample_gpt_validation.to_item()
    gv_converted = itemToGPTValidation(item)
    assert gv_converted == sample_gpt_validation


@pytest.mark.unit
def test_item_to_gpt_validation_missing_keys():
    """Test that itemToGPTValidation raises an error if required keys are missing."""
    incomplete_item = {
        "SK": {"S": "RECEIPT#00007#QUERY#VALIDATION"},
        "query": {"S": "Is this the total amount?"},
        "response": {"S": "Yes, it is the total."},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Item is missing required keys:"):
        itemToGPTValidation(incomplete_item)


@pytest.mark.unit
def test_item_to_gpt_validation_invalid_format():
    """Test that itemToGPTValidation raises an error for an improperly formatted item."""
    bad_item = {
        "PK": {"S": "IMAGE#bad"},  # Improperly formatted
        "SK": {"S": "RECEIPT#00007"},  # Missing query validation suffix
        "TYPE": {"S": "GPT_VALIDATION"},
        "query": {"S": "Is this the total amount?"},
        "response": {"S": "Yes, it is the total."},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Error converting item to GPTValidation"):
        itemToGPTValidation(bad_item)


@pytest.mark.unit
def test_gpt_validation_hash(sample_gpt_validation):
    """Test that __hash__ returns consistent values for equal objects."""
    gv1 = sample_gpt_validation
    gv2 = GPTValidation(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it is the total.",
        timestamp_added="2021-01-01T00:00:00",
    )

    # Equal objects should have equal hash values
    assert hash(gv1) == hash(gv2)

    # Different objects should have different hash values
    gv3 = GPTValidation(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Different query",  # Changed query
        response="Yes, it is the total.",
        timestamp_added="2021-01-01T00:00:00",
    )
    assert hash(gv1) != hash(gv3)
