# test_word_tag.py
import pytest

from receipt_dynamo.entities.word_tag import WordTag, itemToWordTag


@pytest.fixture
def sample_word_tag():
    return WordTag(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=7,
        word_id=101,
        tag="example",
        timestamp_added="2021-01-01T00:00:00",
    )


@pytest.mark.unit
def test_word_tag_init_valid(sample_word_tag):
    """Test that WordTag initializes correct attributes."""
    assert sample_word_tag.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert sample_word_tag.line_id == 7
    assert sample_word_tag.word_id == 101
    assert sample_word_tag.tag == "example"


@pytest.mark.unit
def test_word_tag_init_invalid_image_id():
    """Test that WordTag raises ValueError if image_id is invalid."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        WordTag(1, 2, 3, "example", "2021-01-01T00:00:00")
    with pytest.raises(ValueError, match="uuid must be a valid UUIDv4"):
        WordTag("bad-uuid", 2, 3, "example", "2021-01-01T00:00:00")


@pytest.mark.unit
def test_word_tag_init_invalid_line_id():
    """Test that WordTag raises ValueError if line_id is invalid."""
    with pytest.raises(ValueError, match="line_id must be an integer"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2",
            3,
            "example",
            "2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="line_id must be positive"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            -2,
            3,
            "example",
            "2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_word_tag_init_invalid_word_id():
    """Test that WordTag raises ValueError if word_id is invalid."""
    with pytest.raises(ValueError, match="word_id must be an integer"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            "3",
            "example",
            "2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="word_id must be positive"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            -3,
            "example",
            "2021-01-01T00:00",
        )


@pytest.mark.unit
def test_word_tag_init_invalid_tag():
    """Test that WordTag raises ValueError if tag is invalid."""
    with pytest.raises(ValueError, match="tag must not be empty"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "",
            "2021-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="tag must be a string"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            4,
            "2021-01-01T00:00:00",
        )
    long_tag = "A" * 41
    with pytest.raises(ValueError, match="tag must not exceed 40 characters"):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            long_tag,
            "2021-01-01T00:00:00",
        )
    with pytest.raises(
        ValueError, match="tag must not start with an underscore"
    ):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "_bad",
            "2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_word_tag_init_invalid_timestamp_added():
    """Test that WordTag raises ValueError if timestamp_added is invalid."""
    with pytest.raises(
        ValueError,
        match="timestamp_added must be datetime, str, got",
    ):
        WordTag(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "example", 1234567890
        )


@pytest.mark.unit
def test_word_tag_eq(sample_word_tag):
    """Test __eq__ method—only comparing tag & word_id."""
    wt1 = sample_word_tag
    wt2 = WordTag(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        888,
        101,
        "example",
        timestamp_added="2021-01-01T00:00:00",
    )

    # WordTag equality depends on (word_id, tag)
    assert wt1 == sample_word_tag
    assert wt1 != wt2
    assert wt1 != "another object"


@pytest.mark.unit
def test_word_tag_iter(sample_word_tag):
    """Test __iter__ method yields correct name/value pairs."""
    as_dict = sample_word_tag.to_dict()
    assert as_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert as_dict["line_id"] == 7
    assert as_dict["word_id"] == 101
    assert as_dict["tag"] == "example"


@pytest.mark.unit
def test_word_tag_repr(sample_word_tag):
    """Test __repr__ method includes all relevant data."""
    # e.g. "WordTag(image_id=42, line_id=7, word_id=101, tag=example)"
    representation = repr(sample_word_tag)
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in representation
    assert "line_id=7" in representation
    assert "word_id=101" in representation
    assert "tag='example'" in representation


@pytest.mark.unit
def test_word_tag_key(sample_word_tag):
    """
    Test that the main table key is generated as expected.
    PK = IMAGE#<image_id>
    SK = LINE#<line_id>WORD#<word_id>#TAG#<tag>
    """
    result = sample_word_tag.key()
    assert result["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert (
        result["SK"]["S"]
        == "LINE#00007#WORD#00101#TAG#_________________________________example"
    )


@pytest.mark.unit
def test_word_tag_gsi1_key(sample_word_tag):
    """
    Test that the GSI1 key is generated as expected.
    GSI1PK = TAG#<tag_upper_with_leading_underscores>
    GSI1SK = IMAGE#<image_id>#LINE#<line_id>#WORD#<word_id>
    """
    result = sample_word_tag.gsi1_key()
    assert result["GSI1PK"]["S"].startswith("TAG#")
    assert (
        result["GSI1SK"]["S"]
        == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#LINE#00007#WORD#00101"
    )


@pytest.mark.unit
def test_word_tag_to_item(sample_word_tag):
    """Test that to_item includes PK, SK, GSI1PK, GSI1SK, TYPE."""
    item = sample_word_tag.to_item()
    assert "PK" in item
    assert "SK" in item
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    assert item["TYPE"]["S"] == "WORD_TAG"


@pytest.mark.unit
def test_word_tag_to_word_key(sample_word_tag):
    """Test that the to_Word_key method returns the correct key."""
    key = sample_word_tag.to_Word_key()
    assert key["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert key["SK"]["S"] == "LINE#00007#WORD#00101"


@pytest.mark.unit
def test_item_to_word_tag():
    """
    Test converting a DynamoDB item back to a WordTag object.
    Ensure it properly parses PK, GSI1SK, and extracts the tag.
    """
    item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {
            "S": "LINE#00007"
            "#WORD#00101"
            "#TAG#_________________________________example"
        },
        "GSI1PK": {"S": "TAG#_________________________________example"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
            "#LINE#00007"
            "#WORD#00101"
        },
        "TYPE": {"S": "WORD_TAG"},
        "tag_name": {"S": "example"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "validated": {"BOOL": True},
    }
    wt = itemToWordTag(item)
    assert wt.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert wt.line_id == 7
    assert wt.word_id == 101
    assert wt.tag == "example"
    assert wt.timestamp_added == "2021-01-01T00:00:00"
    assert wt.validated is True


@pytest.mark.unit
def test_item_to_word_tag_missing_keys():
    """Test that itemToWordTag raises an error if PK or SK is missing."""
    incomplete_item = {
        "SK": {"S": "TAG#FOO#LINE#00007#WORD#00101"},
        "GSI1PK": {"S": "TAG#FOO"},
        "GSI1SK": {"S": "IMAGE#00042#LINE#00007#WORD#00101"},
        "TYPE": {"S": "WORD_TAG"},
    }
    with pytest.raises(ValueError, match="^Item is missing required keys: "):
        itemToWordTag(incomplete_item)


@pytest.mark.unit
def test_item_to_word_tag_invalid_format():
    """Test that itemToWordTag raises an error if the key is invalid."""
    bad_format_item = {
        "PK": {"S": "IMAGE#42"},  # Not zero-padded, missing pieces
        "SK": {"S": "TAG#example#WORD#00999"},  # Missing line info
        "GSI1PK": {"S": "TAG#_____________example"},
        "GSI1SK": {"S": "IMAGE#00042#WORD#00999"},  # Also incomplete
        "TYPE": {"S": "WORD_TAG"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "validated": {"BOOL": True},
    }
    with pytest.raises(ValueError, match="Error converting item to WordTag"):
        itemToWordTag(bad_format_item)
