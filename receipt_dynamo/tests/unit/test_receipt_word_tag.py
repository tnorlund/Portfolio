import pytest
from receipt_dynamo.entities.receipt_word_tag import (
    ReceiptWordTag,
    itemToReceiptWordTag,
)


@pytest.fixture
def example_receipt_word_tag():
    """Provides a sample ReceiptWordTag for testing."""
    # fmt: off
    return ReceiptWordTag(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=45,
        line_id=6,
        word_id=789,
        tag="food",
        timestamp_added="2021-01-01T00:00:00",
    )
    # fmt: on


@pytest.mark.unit
def test_receipt_word_tag_init_valid(example_receipt_word_tag):
    """Test constructor initializes the correct fields."""
    assert example_receipt_word_tag.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_receipt_word_tag.receipt_id == 45
    assert example_receipt_word_tag.line_id == 6
    assert example_receipt_word_tag.word_id == 789
    assert example_receipt_word_tag.tag == "food"


@pytest.mark.unit
def test_receipt_word_tag_init_invalid_image_id():
    """Test constructor raises ValueError if image_id is not a valid UUID."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptWordTag(123, 2, 3, 4, "tag", timestamp_added="2021-01-01T00:00:00")
    with pytest.raises(ValueError, match="uuid must be a valid UUIDv4"):
        ReceiptWordTag(
            "bad-uuid", 2, 3, 4, "tag", timestamp_added="2021-01-01T00:00:00"
        )


@pytest.mark.unit
def test_receipt_word_tag_init_invalid_receipt_id():
    """
    Test constructor raises ValueError if receipt_id is not an integer or is
    negative.
    """
    with pytest.raises(ValueError, match="receipt_id must be int, got"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "bad",
            3,
            4,
            "tag",
            timestamp_added="2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            -1,
            3,
            4,
            "tag",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_word_tag_init_invalid_line_id():
    """
    Test constructor raises ValueError if line_id is not an integer or is
    negative.
    """
    with pytest.raises(ValueError, match="line_id must be int, got"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            "bad",
            4,
            "tag",
            timestamp_added="2021-01-01T00:00",
        )
    with pytest.raises(ValueError, match="line_id must be positive"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            -3,
            4,
            "tag",
            timestamp_added="2021-01-01T00:00",
        )


@pytest.mark.unit
def test_receipt_word_tag_init_invalid_word_id():
    """
    Test constructor raises ValueError if word_id is not an integer or is
    negative.
    """
    with pytest.raises(ValueError, match="word_id must be int, got"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "bad",
            "tag",
            timestamp_added="2021-01-01T00:00",
        )
    with pytest.raises(ValueError, match="word_id must be positive"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            -4,
            "tag",
            timestamp_added="2021-01-01T00:00",
        )


@pytest.mark.unit
def test_receipt_word_tag_init_invalid_tag():
    """Test constructor raises ValueError if tag is not a string."""
    with pytest.raises(ValueError, match="tag must not be empty"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            4,
            "",
            "2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="tag must be str, got"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            4,
            123,
            "2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="tag must not exceed 40 characters"):
        long_tag = "A" * 41
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            4,
            long_tag,
            "2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="tag must not start with an underscore"):
        ReceiptWordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            4,
            "_bad",
            "2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_word_tag_init_invalid_timestamp_added():
    """
    Test constructor raises ValueError if timestamp_added is not a datetime
    or string.
    """
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        ReceiptWordTag("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, 4, "tag", 123)


@pytest.mark.unit
def test_receipt_word_tag_eq():
    """
    Test __eq__ method:
      - Two ReceiptWordTags are equal if (image_id, receipt_id, line_id,
        word_id, tag) match.
    """
    # fmt: off
    rwt1 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        30,
        40,
        "TAG",
        timestamp_added="2021-01-01T00:00:00",
    )
    rwt2 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        30,
        40,
        "TAG",
        timestamp_added="2021-01-01T00:00:00",
    )
    rwt3 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        21,
        30,
        40,
        "TAG",
        timestamp_added="2021-01-01T00:00:00",
    )  # different receipt_id
    rwt4 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        31,
        40,
        "TAG",
        timestamp_added="2021-01-01T00:00:00",
    )  # different line_id
    rwt5 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        30,
        41,
        "TAG",
        timestamp_added="2021-01-01T00:00:00",
    )  # different word_id
    rwt6 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        30,
        40,
        "TAg",
        timestamp_added="2022-01-01T00:00:00",
    )  # different tag
    rwt7 = ReceiptWordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        30,
        40,
        "TAG",
        timestamp_added="2022-01-01T00:00:00",
    )  # different timestamp
    # fmt: on

    assert rwt1 == rwt2
    assert rwt1 != rwt3
    assert rwt1 != rwt4
    assert rwt1 != rwt5
    assert rwt1 != rwt6
    assert rwt1 == rwt7, "Different timestamp_added should not affect equality"
    assert rwt1 != "another-type"


@pytest.mark.unit
def test_receipt_word_tag_iter(example_receipt_word_tag):
    """Test __iter__ provides a dict-like iteration over fields."""
    as_dict = example_receipt_word_tag.to_dict()
    assert as_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert as_dict["receipt_id"] == 45
    assert as_dict["line_id"] == 6
    assert as_dict["word_id"] == 789
    assert as_dict["tag"] == "food"


@pytest.mark.unit
def test_receipt_word_tag_repr(example_receipt_word_tag):
    """Test __repr__ includes all relevant fields."""
    result = repr(example_receipt_word_tag)
    assert "ReceiptWordTag" in result
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in result
    assert "receipt_id=45" in result
    assert "line_id=6" in result
    assert "word_id=789" in result
    assert "tag='food'" in result


@pytest.mark.unit
def test_receipt_word_tag_key(example_receipt_word_tag):
    """
    Test that .key() returns the correct PK/SK.
      PK = "IMAGE#<image_id>"
      SK  = "RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>"
    """
    item_key = example_receipt_word_tag.key()
    assert item_key["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert (
        item_key["SK"]["S"] == "RECEIPT#00045#LINE#00006#WORD#00789#TAG#"
        "____________________________________food"
    )


@pytest.mark.unit
def test_receipt_word_tag_gsi1_key(example_receipt_word_tag):
    """
    Test that .gsi1_key() returns GSI1PK/GSI1SK.
      GSI1PK = "TAG#<underscore_padded_tag>"
      GSI1SK = "IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#"
      "WORD#<word_id>"
    """
    gsi_key = example_receipt_word_tag.gsi1_key()
    assert gsi_key["GSI1PK"]["S"].startswith("TAG#")
    assert (
        gsi_key["GSI1SK"]["S"]
        == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00045#"
        "LINE#00006#WORD#00789"
    )


@pytest.mark.unit
def test_receipt_word_tag_gsi2_key(example_receipt_word_tag):
    """
    Test that .gsi2_key() returns GSI2PK/GSI2SK.
      GSI2PK = "RECEIPT"
      GSI2SK = "IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#"
      "WORD#<word_id>#TAG#<tag>"
    """
    gsi_key = example_receipt_word_tag.gsi2_key()
    assert gsi_key["GSI2PK"]["S"] == "RECEIPT"
    assert (
        gsi_key["GSI2SK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#"
        "RECEIPT#00045#"
        "LINE#00006#"
        "WORD#00789#"
        "TAG#"
        "____________________________________food"
    )


@pytest.mark.unit
def test_receipt_word_tag_to_item(example_receipt_word_tag):
    """Test .to_item() combines all keys plus TYPE and tag_name."""
    item = example_receipt_word_tag.to_item()
    # Check primary keys
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert "SK" in item
    # Check GSI keys
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    # Check TYPE
    assert item["TYPE"]["S"] == "RECEIPT_WORD_TAG"
    # Check tag_name
    assert item["tag_name"]["S"] == "food"
    # Check timestamp_added
    assert item["timestamp_added"]["S"] == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_receipt_word_tag_to_receipt_word_key(example_receipt_word_tag):
    """Test the to_ReceiptWord_key method."""
    key = example_receipt_word_tag.to_ReceiptWord_key()
    assert key["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert key["SK"]["S"] == "RECEIPT#00045#LINE#00006#WORD#00789"


@pytest.mark.unit
def test_item_to_receipt_word_tag():
    """
    Test itemToReceiptWordTag reconstructs a ReceiptWordTag from a DynamoDB
    item.
    """
    raw_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {
            "S": "RECEIPT#00005#LINE#00002#WORD#00099#TAG#"
            "____________________________________food"
        },
        "GSI1PK": {"S": "TAG#____________________________________food"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#"
            "RECEIPT#00005#"
            "LINE#00002#"
            "WORD#00099"
        },
        "TYPE": {"S": "RECEIPT_WORD_TAG"},
        "tag_name": {"S": "food"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "validated": {"BOOL": True},
    }

    obj = itemToReceiptWordTag(raw_item)
    assert obj.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert obj.receipt_id == 5
    assert obj.line_id == 2
    assert obj.word_id == 99
    assert obj.tag == "food"
    assert obj.timestamp_added == "2021-01-01T00:00:00"
    assert obj.validated


@pytest.mark.unit
def test_item_to_receipt_word_tag_missing_keys():
    """
    Test that itemToReceiptWordTag raises ValueError if PK, SK, or GSI1SK is
    missing.
    """
    bad_item = {  # "PK" is missing
        "SK": {"S": "TAG#FOO#RECEIPT#00001#WORD#00010"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#"
            "RECEIPT#00001#"
            "LINE#00002#"
            "WORD#00010"
        },
        "TYPE": {"S": "RECEIPT_WORD_TAG"},
    }
    with pytest.raises(ValueError, match="^Item is missing required keys:"):
        itemToReceiptWordTag(bad_item)


@pytest.mark.unit
def test_item_to_receipt_word_tag_invalid_format():
    """
    Test that itemToReceiptWordTag raises ValueError if the PK/SK is badly
    formatted.
    """
    # Missing receipt ID portion or incorrectly formatted SK
    bad_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "TAG#FOOD#WORD#999"},  # missing "RECEIPT#"
        "GSI1PK": {"S": "TAG#FOOD"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#"
            "RECEIPT#00005#"
            "LINE#00006#"
            "WORD#999"
        },
        "TYPE": {"S": "RECEIPT_WORD_TAG"},
        "tag_name": {"S": "food"},
        "timestamp_added": {"N": "2021-01-01T00:00:00"},
        "validated": {"BOOL": True},
    }
    with pytest.raises(ValueError, match="Error converting item to ReceiptWordTag"):
        itemToReceiptWordTag(bad_item)
