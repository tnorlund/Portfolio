import pytest
from dynamo.entities.receipt_word_tag import ReceiptWordTag, itemToReceiptWordTag

@pytest.fixture
def example_receipt_word_tag():
    """Provides a sample ReceiptWordTag for testing."""
    return ReceiptWordTag(
        image_id=123,
        receipt_id=45,
        line_id=6,
        word_id=789,
        tag="food"
    )

def test_receipt_word_tag_init(example_receipt_word_tag):
    """Test constructor initializes the correct fields."""
    assert example_receipt_word_tag.image_id == 123
    assert example_receipt_word_tag.receipt_id == 45
    assert example_receipt_word_tag.line_id == 6
    assert example_receipt_word_tag.word_id == 789
    assert example_receipt_word_tag.tag == "food"


def test_receipt_word_tag_init_empty_tag():
    """Test constructor raises ValueError if tag is empty."""
    with pytest.raises(ValueError, match="tag must not be empty"):
        ReceiptWordTag(1, 2, 3, 4, "")


def test_receipt_word_tag_init_long_tag():
    """Test constructor raises ValueError if tag is too long (>20 chars)."""
    long_tag = "A" * 21
    with pytest.raises(ValueError, match="tag must not exceed 20 characters"):
        ReceiptWordTag(1, 2, 3, 4, long_tag)


def test_receipt_word_tag_init_underscore_tag():
    """Test constructor raises ValueError if tag starts with underscore."""
    with pytest.raises(ValueError, match="tag must not start with an underscore"):
        ReceiptWordTag(1, 2, 3, 4, "_bad")


def test_receipt_word_tag_eq():
    """
    Test __eq__ method:
      - Two ReceiptWordTags are equal if (word_id, receipt_id, tag) match.
    """
    rwt1 = ReceiptWordTag(10, 20, 30, 40, "TAG")
    rwt2 = ReceiptWordTag(999, 20, 888, 40, "TAG")   # same receipt_id, word_id, tag
    rwt3 = ReceiptWordTag(10, 20, 30, 999, "OTHER")

    assert rwt1 == rwt2
    assert rwt1 != rwt3


def test_receipt_word_tag_iter(example_receipt_word_tag):
    """Test __iter__ provides a dict-like iteration over fields."""
    as_dict = dict(example_receipt_word_tag)
    assert as_dict["image_id"] == 123
    assert as_dict["receipt_id"] == 45
    assert as_dict["line_id"] == 6
    assert as_dict["word_id"] == 789
    assert as_dict["tag"] == "food"


def test_receipt_word_tag_repr(example_receipt_word_tag):
    """Test __repr__ includes all relevant fields."""
    result = repr(example_receipt_word_tag)
    assert "ReceiptWordTag" in result
    assert "image_id=123" in result
    assert "receipt_id=45" in result
    assert "line_id=6" in result
    assert "word_id=789" in result
    assert "tag=food" in result


def test_receipt_word_tag_key(example_receipt_word_tag):
    """
    Test that .key() returns the correct PK/SK.
      PK = "IMAGE#<image_id>"
      SK = "TAG#<underscore_padded_tag>#RECEIPT#<receipt_id>#WORD#<word_id>"
    """
    item_key = example_receipt_word_tag.key()
    assert item_key["PK"]["S"] == "IMAGE#00123"
    # tag="food" => uppercase => "FOOD" => underscore-padded to 20 chars => "________________FOOD" (some underscores).
    # We'll just check it starts with "TAG#" and ends with "#RECEIPT#00045#WORD#00789"
    sk_val = item_key["SK"]["S"]
    assert sk_val.startswith("TAG#")
    assert sk_val.endswith("#RECEIPT#00045#WORD#00789")


def test_receipt_word_tag_gsi1_key(example_receipt_word_tag):
    """
    Test that .gsi1_key() returns GSI1PK/GSI1SK.
      GSI1PK = "TAG#<underscore_padded_tag>"
      GSI1SK = "IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>"
    """
    gsi_key = example_receipt_word_tag.gsi1_key()
    assert gsi_key["GSI1PK"]["S"].startswith("TAG#")
    assert gsi_key["GSI1SK"]["S"] == "IMAGE#00123#RECEIPT#00045#LINE#00006#WORD#00789"


def test_receipt_word_tag_to_item(example_receipt_word_tag):
    """Test .to_item() combines all keys plus TYPE and tag_name."""
    item = example_receipt_word_tag.to_item()

    # Check primary keys
    assert item["PK"]["S"] == "IMAGE#00123"
    assert "SK" in item

    # Check GSI keys
    assert "GSI1PK" in item
    assert "GSI1SK" in item

    # Check TYPE
    assert item["TYPE"]["S"] == "RECEIPT_WORD_TAG"

    # Check tag_name
    assert item["tag_name"]["S"] == "FOOD"


def test_item_to_receipt_word_tag():
    """
    Test itemToReceiptWordTag reconstructs a ReceiptWordTag from a DynamoDB item.
    """
    raw_item = {
        "PK": {"S": "IMAGE#00012"},
        "SK": {"S": "TAG#____________FOOD#RECEIPT#00005#WORD#00099"},
        "GSI1PK": {"S": "TAG#____________FOOD"},
        "GSI1SK": {"S": "IMAGE#00012#RECEIPT#00005#LINE#00002#WORD#00099"},
        "TYPE": {"S": "RECEIPT_WORD_TAG"},
        "tag_name": {"S": "FOOD"},
    }

    obj = itemToReceiptWordTag(raw_item)
    assert obj.image_id == 12
    assert obj.receipt_id == 5
    assert obj.line_id == 2
    assert obj.word_id == 99
    assert obj.tag == "FOOD"


def test_item_to_receipt_word_tag_missing_keys():
    """
    Test that itemToReceiptWordTag raises ValueError if PK, SK, or GSI1SK is missing.
    """
    bad_item = {
        # "PK" is missing
        "SK": {"S": "TAG#FOO#RECEIPT#00001#WORD#00010"},
        "GSI1SK": {"S": "IMAGE#00012#RECEIPT#00001#LINE#00002#WORD#00010"},
        "TYPE": {"S": "RECEIPT_WORD_TAG"},
    }
    with pytest.raises(ValueError, match="missing required keys"):
        itemToReceiptWordTag(bad_item)


def test_item_to_receipt_word_tag_bad_format():
    """
    Test that itemToReceiptWordTag raises ValueError if the PK/SK is badly formatted.
    """
    # Missing receipt ID portion or incorrectly formatted SK
    bad_item = {
        "PK": {"S": "IMAGE#00123"},
        "SK": {"S": "TAG#FOOD#WORD#999"},  # missing "RECEIPT#"
        "GSI1PK": {"S": "TAG#FOOD"},
        "GSI1SK": {"S": "IMAGE#00123#RECEIPT#00005#LINE#00006#WORD#999"},
        "TYPE": {"S": "RECEIPT_WORD_TAG"},
    }
    with pytest.raises(ValueError, match="missing or has invalid values"):
        itemToReceiptWordTag(bad_item)