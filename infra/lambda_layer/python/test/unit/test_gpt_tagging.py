# test_gpt_tagging.py
import pytest
from datetime import datetime
from dynamo.entities.gpt_initial_tagging import GPTInitialTagging, itemToGPTInitialTagging

# --- Fixtures ---

@pytest.fixture
def sample_gpt_tagging():
    """
    Returns a sample GPTInitialTagging instance with valid attributes.
    """
    return GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        line_id=3,
        word_id=15,
        tag="TOTAL",
        query="Is this the total amount?",
        response="Yes, it appears to be the total.",
        timestamp_added="2021-01-01T00:00:00"
    )

# --- Initialization Tests ---

@pytest.mark.unit
def test_gpt_tagging_init(sample_gpt_tagging):
    """Test that GPTInitialTagging initializes correct attributes."""
    gt = sample_gpt_tagging
    assert gt.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert gt.receipt_id == 7
    assert gt.line_id == 3
    assert gt.word_id == 15
    assert gt.tag == "TOTAL"
    assert gt.query == "Is this the total amount?"
    assert gt.response == "Yes, it appears to be the total."
    assert gt.timestamp_added == "2021-01-01T00:00:00"

@pytest.mark.unit
def test_gpt_tagging_init_bad_image_id():
    """Test that GPTInitialTagging raises ValueError for an invalid image_id."""
    with pytest.raises(ValueError):
        GPTInitialTagging(
            image_id=1,  # not a string
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError):
        GPTInitialTagging(
            image_id="bad-uuid",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_receipt_id():
    """Test that GPTInitialTagging raises ValueError for an invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id="7",  # wrong type
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,  # negative value
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_line_id():
    """Test that GPTInitialTagging raises ValueError for an invalid line_id."""
    with pytest.raises(ValueError, match="line_id must be an integer"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id="3",  # wrong type
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="line_id must be positive"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=-1,  # negative value
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_word_id():
    """Test that GPTInitialTagging raises ValueError for an invalid word_id."""
    with pytest.raises(ValueError, match="word_id must be an integer"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id="15",  # wrong type
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="word_id must be positive"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=-5,  # negative value
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_tag():
    """Test that GPTInitialTagging raises ValueError for an invalid tag."""
    with pytest.raises(ValueError, match="tag must not be empty"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="tag must be a string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag=123,
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    long_tag = "A" * 41
    with pytest.raises(ValueError, match="tag must not exceed 40 characters"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag=long_tag,
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="tag must not start with an underscore"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="_BAD",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_query():
    """Test that GPTInitialTagging raises ValueError for an invalid query."""
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="",
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query=123,
            response="Yes, it appears to be the total.",
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_response():
    """Test that GPTInitialTagging raises ValueError for an invalid response."""
    with pytest.raises(ValueError, match="response must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="",
            timestamp_added="2021-01-01T00:00:00"
        )
    with pytest.raises(ValueError, match="response must be a non-empty string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response=123,
            timestamp_added="2021-01-01T00:00:00"
        )

@pytest.mark.unit
def test_gpt_tagging_init_bad_timestamp():
    """Test that GPTInitialTagging raises ValueError for an invalid timestamp_added."""
    with pytest.raises(ValueError, match="timestamp_added must be a datetime object or a string"):
        GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7,
            line_id=3,
            word_id=15,
            tag="TOTAL",
            query="Is this the total amount?",
            response="Yes, it appears to be the total.",
            timestamp_added=1234567890
        )

# --- Equality, Iteration, and Representation Tests ---

@pytest.mark.unit
def test_gpt_tagging_eq(sample_gpt_tagging):
    """Test __eq__ method for GPTInitialTagging."""
    gt1 = sample_gpt_tagging
    gt2 = GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        line_id=3,
        word_id=15,
        tag="TOTAL",
        query="Is this the total amount?",
        response="Yes, it appears to be the total.",
        timestamp_added="2021-01-01T00:00:00"
    )
    assert gt1 == gt2

    gt3 = GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        line_id=3,
        word_id=15,
        tag="TOTAL",
        query="Different query",
        response="Yes, it appears to be the total.",
        timestamp_added="2021-01-01T00:00:00"
    )
    assert gt1 != gt3
    assert gt1 != "not a GPTInitialTagging"

@pytest.mark.unit
def test_gpt_tagging_iter(sample_gpt_tagging):
    """Test that __iter__ yields the correct attribute key/value pairs."""
    as_dict = dict(sample_gpt_tagging)
    assert as_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert as_dict["receipt_id"] == 7
    assert as_dict["line_id"] == 3
    assert as_dict["word_id"] == 15
    assert as_dict["tag"] == "TOTAL"
    assert as_dict["query"] == "Is this the total amount?"
    assert as_dict["response"] == "Yes, it appears to be the total."
    assert as_dict["timestamp_added"] == "2021-01-01T00:00:00"

@pytest.mark.unit
def test_gpt_tagging_repr(sample_gpt_tagging):
    """Test that __repr__ includes relevant attribute data."""
    rep = repr(sample_gpt_tagging)
    assert "image_id=" in rep
    assert "receipt_id=7" in rep
    assert "line_id=3" in rep
    assert "word_id=15" in rep
    assert "tag=" in rep
    assert "TOTAL" in rep
    assert "query=" in rep
    assert "response=" in rep

# --- Key Generation and Item Conversion Tests ---

@pytest.mark.unit
def test_gpt_tagging_key(sample_gpt_tagging):
    """Test that the primary key is generated as expected."""
    key = sample_gpt_tagging.key()
    assert key["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    spaced_tag = f"{'TOTAL':_>40}"
    expected_sk = (
        f"RECEIPT#{7:05d}"
        f"#LINE#{3:05d}"
        f"#WORD#{15:05d}"
        f"#TAG#{spaced_tag}"
        f"#QUERY#INITIAL_TAGGING"
    )
    assert key["SK"]["S"] == expected_sk

@pytest.mark.unit
def test_gpt_tagging_gsi1_key(sample_gpt_tagging):
    """Test that the GSI1 key is generated as expected."""
    gsi1 = sample_gpt_tagging.gsi1_key()
    spaced_tag = f"{'TOTAL':_>40}"
    expected_gsi1pk = f"TAG#{spaced_tag}"
    expected_gsi1sk = (
        f"IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
        f"#RECEIPT#{7:05d}"
        f"#LINE#{3:05d}"
        f"#WORD#{15:05d}"
    )
    assert gsi1["GSI1PK"]["S"] == expected_gsi1pk
    assert gsi1["GSI1SK"]["S"] == expected_gsi1sk

@pytest.mark.unit
def test_gpt_tagging_to_item(sample_gpt_tagging):
    """Test that to_item returns a DynamoDB-ready dictionary."""
    item = sample_gpt_tagging.to_item()
    assert "PK" in item
    assert "SK" in item
    assert "GSI1PK" in item
    assert "GSI1SK" in item
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
    incomplete_item = {
        "SK": {"S": "RECEIPT#00007#LINE#00003#WORD#00015#TAG#___________________________________TOTAL#QUERY#INITIAL_TAGGING"},
        "query": {"S": "Is this the total amount?"},
        "response": {"S": "Yes, it appears to be the total."},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Item is missing required keys:"):
        itemToGPTInitialTagging(incomplete_item)

@pytest.mark.unit
def test_item_to_gpt_tagging_bad_format():
    """Test that itemToGPTInitialTagging raises an error for an improperly formatted item."""
    bad_item = {
        "PK": {"S": "IMAGE#bad"},  # Improperly formatted
        "SK": {"S": "RECEIPT#00007#LINE#00003#WORD#00015"},  # Missing tag and query suffix
        "GSI1PK": {"S": "TAG#___________________________________TOTAL"},
        "GSI1SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00007#LINE#00003#WORD#00015"},
        "TYPE": {"S": "GPT_INITIAL_TAGGING"},
        "query": {"S": "Is this the total amount?"},
        "response": {"S": "Yes, it appears to be the total."},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Error converting item to GPTInitialTagging"):
        itemToGPTInitialTagging(bad_item)