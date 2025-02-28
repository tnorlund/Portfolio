# test_gpt_tagging.py
import pytest

from receipt_dynamo.entities.gpt_initial_tagging import (
    GPTInitialTagging,
    itemToGPTInitialTagging,
)

# --- Fixtures ---


@pytest.fixture
def sample_gpt_tagging():
    """
    Returns a sample GPTInitialTagging instance with valid attributes.
    """
    return GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it appears to be the total.",
        timestamp_added="2021-01-01T00:00:00",
    )


# --- Initialization Tests ---


@pytest.mark.unit
def test_gpt_tagging_init_valid(sample_gpt_tagging):
    """Test that GPTInitialTagging initializes correct attributes."""
    gt = sample_gpt_tagging
    assert gt.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert gt.receipt_id == 7
    assert gt.query == "Is this the total amount?"
    assert gt.response == "Yes, it appears to be the total."
    assert gt.timestamp_added == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_gpt_tagging_init_invalid_image_id():
    """Test that GPTInitialTagging raises ValueError for an invalid image_id."""
    with pytest.raises(ValueError):
        GPTInitialTagging(
            image_id=1,  # not a string
            receipt_id=7,
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError):
        GPTInitialTagging(
            image_id="bad-uuid",
            receipt_id=7,
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_tagging_init_invalid_receipt_id():
    """Test that GPTInitialTagging raises ValueError for an invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id="7",  # wrong type
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,  # negative
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_tagging_init_invalid_query():
    """Test that GPTInitialTagging raises ValueError for an invalid query."""
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query=123,  # not a string
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_tagging_init_invalid_response():
    """Test that GPTInitialTagging raises ValueError for an invalid response."""
    with pytest.raises(ValueError, match="response must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="Is this the total amount?",
            response="",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="response must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="Is this the total amount?",
            response=123,  # not a string
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_gpt_tagging_init_invalid_timestamp():
    """Test that GPTInitialTagging raises ValueError for an invalid timestamp_added."""
    with pytest.raises(
        ValueError, match="timestamp_added must be a datetime object or a string"
    ):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added=1234567890,
        )  # not a datetime or string


# --- Equality, Iteration, and Representation Tests ---


@pytest.mark.unit
def test_gpt_tagging_eq(sample_gpt_tagging):
    """Test __eq__ method for GPTInitialTagging."""
    gt1 = sample_gpt_tagging
    gt2 = GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it appears to be the total.",
        timestamp_added="2021-01-01T00:00:00",
    )
    assert gt1 == gt2

    gt3 = GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Different query",
        response="Yes, it appears to be the total.",
        timestamp_added="2021-01-01T00:00:00",
    )
    assert gt1 != gt3
    assert gt1 != "not a GPTInitialTagging"


@pytest.mark.unit
def test_gpt_tagging_iter(sample_gpt_tagging):
    """Test that __iter__ yields the correct attribute key/value pairs."""
    as_dict = dict(sample_gpt_tagging)
    assert as_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert as_dict["receipt_id"] == 7
    assert as_dict["query"] == "Is this the total amount?"
    assert as_dict["response"] == "Yes, it appears to be the total."
    assert as_dict["timestamp_added"] == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_gpt_tagging_repr(sample_gpt_tagging):
    """Test that __repr__ includes relevant attribute data."""
    rep = repr(sample_gpt_tagging)
    assert "image_id=" in rep
    assert "receipt_id=7" in rep
    assert "query=" in rep
    assert "response=" in rep
    assert "timestamp_added=" in rep


# --- Key Generation and Item Conversion Tests ---


@pytest.mark.unit
def test_gpt_tagging_key(sample_gpt_tagging):
    """Test that the primary key is generated correctly for a one-record-per-receipt design."""
    key = sample_gpt_tagging.key()
    assert key["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert key["SK"]["S"] == "RECEIPT#00007#QUERY#INITIAL_TAGGING"


@pytest.mark.unit
def test_gpt_tagging_to_item(sample_gpt_tagging):
    """Test that to_item returns a DynamoDB-ready dictionary (no GSI references)."""
    item = sample_gpt_tagging.to_item()
    assert "PK" in item
    assert "SK" in item
    assert "TYPE" in item
    assert item["TYPE"]["S"] == "GPT_INITIAL_TAGGING"
    assert item["query"]["S"] == "Is this the total amount?"
    assert item["response"]["S"] == "Yes, it appears to be the total."
    assert item["timestamp_added"]["S"] == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_item_to_gpt_tagging(sample_gpt_tagging):
    """Test that a DynamoDB item converts back to a GPTInitialTagging object."""
    item = sample_gpt_tagging.to_item()
    gt_converted = itemToGPTInitialTagging(item)
    assert gt_converted == sample_gpt_tagging


@pytest.mark.unit
def test_item_to_gpt_tagging_missing_keys():
    """Test that itemToGPTInitialTagging raises an error if required keys are missing."""
    incomplete_item = {  # Missing PK
        "SK": {"S": "RECEIPT#00007#QUERY#INITIAL_TAGGING"},
        "query": {"S": "Is this the total amount?"},
        "response": {"S": "Yes, it appears to be the total."},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Item is missing required keys:"):
        itemToGPTInitialTagging(incomplete_item)


@pytest.mark.unit
def test_item_to_gpt_tagging_invalid_format():
    """Test that itemToGPTInitialTagging raises an error for an improperly formatted item."""
    bad_item = {
        "PK": {"S": "IMAGE#bad"},  # e.g. missing or incomplete ID
        "SK": {"S": "RECEIPT#00007"},  # missing '#QUERY#INITIAL_TAGGING'
        "TYPE": {"S": "GPT_INITIAL_TAGGING"},
        "query": {"S": "Is this the total amount?"},
        "response": {"S": "Yes, it appears to be the total."},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Error converting item to GPTInitialTagging"):
        itemToGPTInitialTagging(bad_item)
