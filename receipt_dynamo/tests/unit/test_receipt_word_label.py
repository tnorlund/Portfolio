from datetime import datetime

import pytest

from receipt_dynamo import ReceiptWordLabel, itemToReceiptWordLabel


@pytest.fixture
def example_receipt_word_label():
    return ReceiptWordLabel(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        line_id=2,
        word_id=3,
        label="BUSINESS_NAME",
        reasoning="This word appears at the top of the receipt and matches known business name patterns",
        timestamp_added="2021-01-01T00:00:00",
    )


@pytest.mark.unit
def test_receipt_word_label_init_valid(example_receipt_word_label):
    """Test constructing a valid ReceiptWordLabel."""
    assert (
        example_receipt_word_label.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_word_label.receipt_id == 1
    assert example_receipt_word_label.line_id == 2
    assert example_receipt_word_label.word_id == 3
    assert example_receipt_word_label.label == "BUSINESS_NAME"
    assert (
        example_receipt_word_label.reasoning
        == "This word appears at the top of the receipt and matches known business name patterns"
    )
    assert example_receipt_word_label.timestamp_added == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_receipt_word_label_init_invalid_image_id():
    """ReceiptWordLabel with invalid image_id raises ValueError."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptWordLabel(
            image_id=1,
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="BUSINESS_NAME",
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_word_label_init_invalid_ids():
    """Test that constructing a ReceiptWordLabel with invalid ids raises ValueError."""
    # Invalid receipt_id
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,
            line_id=2,
            word_id=3,
            label="BUSINESS_NAME",
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Invalid line_id
    with pytest.raises(ValueError, match="line_id must be positive"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=-2,
            word_id=3,
            label="BUSINESS_NAME",
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Invalid word_id
    with pytest.raises(ValueError, match="word_id must be positive"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            word_id=-3,
            label="BUSINESS_NAME",
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_word_label_init_invalid_label():
    """ReceiptWordLabel with invalid label raises ValueError."""
    # Empty label
    with pytest.raises(ValueError, match="label cannot be empty"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="",
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Non-string label
    with pytest.raises(ValueError, match="label must be a string"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            word_id=3,
            label=123,
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_word_label_init_invalid_reasoning():
    """ReceiptWordLabel with invalid reasoning raises ValueError."""
    # Empty reasoning
    with pytest.raises(ValueError, match="reasoning cannot be empty"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="BUSINESS_NAME",
            reasoning="",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Non-string reasoning
    with pytest.raises(ValueError, match="reasoning must be a string"):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="BUSINESS_NAME",
            reasoning=123,
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_word_label_init_valid_timestamp():
    """Test that constructing a ReceiptWordLabel with a valid timestamp works."""
    ReceiptWordLabel(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        line_id=2,
        word_id=3,
        label="BUSINESS_NAME",
        reasoning="Test reasoning",
        timestamp_added=datetime.now(),
    )


@pytest.mark.unit
def test_receipt_word_label_init_invalid_timestamp():
    """Constructing a ReceiptWordLabel with an invalid timestamp raises ValueError."""
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        ReceiptWordLabel(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="BUSINESS_NAME",
            reasoning="Test reasoning",
            timestamp_added=123,
        )


@pytest.mark.unit
def test_receipt_word_label_key_generation(example_receipt_word_label):
    """Test that the primary key is correctly generated."""
    assert example_receipt_word_label.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#BUSINESS_NAME"},
    }


@pytest.mark.unit
def test_receipt_word_label_gsi1_key_generation(example_receipt_word_label):
    """Test that the GSI1 key is correctly generated."""
    assert example_receipt_word_label.gsi1_key() == {
        "GSI1PK": {"S": "LABEL#BUSINESS_NAME_____________________"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#LINE#00002#WORD#00003"
        },
    }


@pytest.mark.unit
def test_receipt_word_label_to_item(example_receipt_word_label):
    """Test converting a ReceiptWordLabel to a DynamoDB item."""
    assert example_receipt_word_label.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#BUSINESS_NAME"},
        "GSI1PK": {"S": "LABEL#BUSINESS_NAME_____________________"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#LINE#00002#WORD#00003"
        },
        "TYPE": {"S": "RECEIPT_WORD_LABEL"},
        "reasoning": {
            "S": "This word appears at the top of the receipt and matches known business name patterns"
        },
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }


@pytest.mark.unit
def test_receipt_word_label_repr(example_receipt_word_label):
    """Test the string representation of a ReceiptWordLabel."""
    assert str(example_receipt_word_label) == (
        "ReceiptWordLabel("
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "receipt_id=1, "
        "line_id=2, "
        "word_id=3, "
        "label='BUSINESS_NAME', "
        "reasoning='This word appears at the top of the receipt and matches known business name patterns', "
        "timestamp_added='2021-01-01T00:00:00'"
        ")"
    )


@pytest.mark.unit
def test_receipt_word_label_iter(example_receipt_word_label):
    """Test that ReceiptWordLabel is iterable."""
    assert dict(example_receipt_word_label) == {
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "receipt_id": 1,
        "line_id": 2,
        "word_id": 3,
        "label": "BUSINESS_NAME",
        "reasoning": "This word appears at the top of the receipt and matches known business name patterns",
        "timestamp_added": "2021-01-01T00:00:00",
    }
    assert (
        ReceiptWordLabel(**dict(example_receipt_word_label))
        == example_receipt_word_label
    )


@pytest.mark.unit
def test_receipt_word_label_eq(example_receipt_word_label):
    """Test that ReceiptWordLabel equality works as expected."""
    assert example_receipt_word_label == ReceiptWordLabel(
        **dict(example_receipt_word_label)
    )
    assert example_receipt_word_label != ReceiptWordLabel(
        **dict(example_receipt_word_label, receipt_id=2)
    )
    assert example_receipt_word_label is not None


@pytest.mark.unit
def test_item_to_receipt_word_label_valid_input(example_receipt_word_label):
    """Test itemToReceiptWordLabel with a valid DynamoDB item."""
    assert (
        itemToReceiptWordLabel(example_receipt_word_label.to_item())
        == example_receipt_word_label
    )


@pytest.mark.unit
def test_item_to_receipt_word_label_missing_keys():
    """itemToReceiptWordLabel raises ValueError when required keys are missing."""
    incomplete_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#BUSINESS_NAME"},
    }
    with pytest.raises(ValueError, match="Invalid item format\nmissing keys"):
        itemToReceiptWordLabel(incomplete_item)


@pytest.mark.unit
def test_item_to_receipt_word_label_invalid_format():
    """itemToReceiptWordLabel raises ValueError when keys are incorrectly formatted."""
    invalid_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#BUSINESS_NAME"},
        "reasoning": {"N": "123"},  # Should be {"S": "123"}
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to ReceiptWordLabel"
    ):
        itemToReceiptWordLabel(invalid_item)
