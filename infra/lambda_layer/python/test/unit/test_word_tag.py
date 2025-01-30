# test_word_tag.py
import pytest
from dynamo.entities.word_tag import WordTag, itemToWordTag


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
def test_word_tag_init(sample_word_tag):
    """Test that WordTag initializes correct attributes."""
    assert sample_word_tag.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert sample_word_tag.line_id == 7
    assert sample_word_tag.word_id == 101
    assert sample_word_tag.tag == "example"


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


@pytest.mark.unit
def test_word_tag_iter(sample_word_tag):
    """Test __iter__ method yields correct name/value pairs."""
    as_dict = dict(sample_word_tag)
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
def test_item_to_word_tag():
    """
    Test converting a DynamoDB item back to a WordTag object.
    Ensure it properly parses PK, GSI1SK, and extracts the tag.
    """
    item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {
            "S": "LINE#00007#WORD#00101#TAG#_________________________________example"
        },
        "GSI1PK": {"S": "TAG#_________________________________example"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#LINE#00007#WORD#00101"
        },
        "TYPE": {"S": "WORD_TAG"},
        "tag_name": {"S": "example"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    wt = itemToWordTag(item)
    assert wt.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert wt.line_id == 7
    assert wt.word_id == 101
    assert wt.tag == "example"  # note itemToWordTag uses upper() from GSI1SK


@pytest.mark.unit
def test_item_to_word_tag_missing_keys():
    """Test that itemToWordTag raises an error if PK or SK is missing."""
    incomplete_item = {
        "SK": {"S": "TAG#FOO#LINE#00007#WORD#00101"},
        "GSI1PK": {"S": "TAG#FOO"},
        "GSI1SK": {"S": "IMAGE#00042#LINE#00007#WORD#00101"},
        "TYPE": {"S": "WORD_TAG"},
    }
    with pytest.raises(ValueError) as exc_info:
        itemToWordTag(incomplete_item)

    assert "missing required keys" in str(exc_info.value)


@pytest.mark.unit
def test_item_to_word_tag_bad_format():
    """Test that itemToWordTag raises an error if the PK or SK format is invalid."""
    bad_format_item = {
        "PK": {"S": "IMAGE#42"},  # Not zero-padded, missing pieces
        "SK": {"S": "TAG#example#WORD#00999"},  # Missing line info
        "GSI1PK": {"S": "TAG#_____________example"},
        "GSI1SK": {"S": "IMAGE#00042#WORD#00999"},  # Also incomplete
        "TYPE": {"S": "WORD_TAG"},
    }
    with pytest.raises(ValueError, match="missing required values"):
        itemToWordTag(bad_format_item)
