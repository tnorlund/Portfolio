from datetime import datetime

import pytest

from receipt_dynamo import ReceiptField, item_to_receipt_field


@pytest.fixture
def example_receipt_field():
    return ReceiptField(
        field_type="BUSINESS_NAME",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        words=[
            {
                "word_id": 1,
                "line_id": 1,
                "label": "BUSINESS_NAME",
            },
            {
                "word_id": 2,
                "line_id": 1,
                "label": "BUSINESS_NAME",
            },
        ],
        reasoning="These words appear at the top of the receipt and match known business name patterns",
        timestamp_added="2021-01-01T00:00:00",
    )


@pytest.mark.unit
def test_receipt_field_init_valid(example_receipt_field):
    """Test constructing a valid ReceiptField."""
    assert example_receipt_field.field_type == "BUSINESS_NAME"
    assert example_receipt_field.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_receipt_field.receipt_id == 1
    assert len(example_receipt_field.words) == 2
    assert example_receipt_field.words[0]["word_id"] == 1
    assert example_receipt_field.words[0]["line_id"] == 1
    assert example_receipt_field.words[0]["label"] == "BUSINESS_NAME"
    assert (
        example_receipt_field.reasoning
        == "These words appear at the top of the receipt and match known business name patterns"
    )
    assert example_receipt_field.timestamp_added == "2021-01-01T00:00:00"


@pytest.mark.unit
def test_receipt_field_init_invalid_field_type():
    """ReceiptField with invalid field_type raises ValueError."""
    # Empty field_type
    with pytest.raises(ValueError, match="field_type cannot be empty"):
        ReceiptField(
            field_type="",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Non-string field_type
    with pytest.raises(ValueError, match="field_type must be a string"):
        ReceiptField(
            field_type=123,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_field_init_invalid_image_id():
    """ReceiptField with invalid image_id raises ValueError."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id=1,
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_field_init_invalid_receipt_id():
    """Test that constructing a ReceiptField with invalid receipt_id raises ValueError."""
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_field_init_invalid_words():
    """Test that constructing a ReceiptField with invalid words raises ValueError."""
    # Non-list words
    with pytest.raises(ValueError, match="words must be a list"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words="not a list",
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Non-dict word
    with pytest.raises(ValueError, match="each word must be a dictionary"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=["not a dict"],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Missing required keys
    with pytest.raises(ValueError, match="word missing required keys"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1}],  # Missing line_id and label
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Invalid word_id
    with pytest.raises(ValueError, match="word_id must be a positive integer"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": -1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Invalid line_id
    with pytest.raises(ValueError, match="line_id must be a positive integer"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": -1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Invalid label
    with pytest.raises(ValueError, match="label must be a non-empty string"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": ""}],
            reasoning="Test reasoning",
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_field_init_invalid_reasoning():
    """ReceiptField with invalid reasoning raises ValueError."""
    # Empty reasoning
    with pytest.raises(ValueError, match="reasoning cannot be empty"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="",
            timestamp_added="2021-01-01T00:00:00",
        )

    # Non-string reasoning
    with pytest.raises(ValueError, match="reasoning must be a string"):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning=123,
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_receipt_field_init_valid_timestamp():
    """Test that constructing a ReceiptField with a valid timestamp works."""
    ReceiptField(
        field_type="BUSINESS_NAME",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
        reasoning="Test reasoning",
        timestamp_added=datetime.now(),
    )


@pytest.mark.unit
def test_receipt_field_init_invalid_timestamp():
    """Constructing a ReceiptField with an invalid timestamp raises ValueError."""
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[{"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"}],
            reasoning="Test reasoning",
            timestamp_added=123,
        )


@pytest.mark.unit
def test_receipt_field_key_generation(example_receipt_field):
    """Test that the primary key is correctly generated."""
    assert example_receipt_field.key == {
        "PK": {"S": "FIELD#BUSINESS_NAME"},
        "SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
    }


@pytest.mark.unit
def test_receipt_field_gsi1_key_generation(example_receipt_field):
    """Test that the GSI1 key is correctly generated."""
    assert example_receipt_field.gsi1_key() == {
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "RECEIPT#00001#FIELD#BUSINESS_NAME"},
    }


@pytest.mark.unit
def test_receipt_field_to_item(example_receipt_field):
    """Test converting a ReceiptField to a DynamoDB item."""
    assert example_receipt_field.to_item() == {
        "PK": {"S": "FIELD#BUSINESS_NAME"},
        "SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "RECEIPT#00001#FIELD#BUSINESS_NAME"},
        "TYPE": {"S": "RECEIPT_FIELD"},
        "words": {
            "L": [
                {
                    "M": {
                        "word_id": {"N": "1"},
                        "line_id": {"N": "1"},
                        "label": {"S": "BUSINESS_NAME"},
                    }
                },
                {
                    "M": {
                        "word_id": {"N": "2"},
                        "line_id": {"N": "1"},
                        "label": {"S": "BUSINESS_NAME"},
                    }
                },
            ]
        },
        "reasoning": {
            "S": "These words appear at the top of the receipt and match known business name patterns"
        },
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }


@pytest.mark.unit
def test_receipt_field_repr(example_receipt_field):
    """Test the string representation of a ReceiptField."""
    assert str(example_receipt_field) == (
        "ReceiptField("
        "field_type='BUSINESS_NAME', "
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "receipt_id=1, "
        "words=[{'word_id': 1, 'line_id': 1, 'label': 'BUSINESS_NAME'}, "
        "{'word_id': 2, 'line_id': 1, 'label': 'BUSINESS_NAME'}], "
        "reasoning='These words appear at the top of the receipt and match known business name patterns', "
        "timestamp_added='2021-01-01T00:00:00'"
        ")"
    )


@pytest.mark.unit
def test_receipt_field_iter(example_receipt_field):
    """Test that ReceiptField is iterable."""
    assert dict(example_receipt_field) == {
        "field_type": "BUSINESS_NAME",
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "receipt_id": 1,
        "words": [
            {"word_id": 1, "line_id": 1, "label": "BUSINESS_NAME"},
            {"word_id": 2, "line_id": 1, "label": "BUSINESS_NAME"},
        ],
        "reasoning": "These words appear at the top of the receipt and match known business name patterns",
        "timestamp_added": "2021-01-01T00:00:00",
    }
    assert ReceiptField(**dict(example_receipt_field)) == example_receipt_field


@pytest.mark.unit
def test_receipt_field_eq(example_receipt_field):
    """Test that ReceiptField equality works as expected."""
    assert example_receipt_field == ReceiptField(**dict(example_receipt_field))
    assert example_receipt_field != ReceiptField(
        **dict(example_receipt_field, receipt_id=2)
    )
    assert example_receipt_field is not None


@pytest.mark.unit
def test_item_to_receipt_field_valid_input(example_receipt_field):
    """Test item_to_receipt_field with a valid DynamoDB item."""
    assert (
        item_to_receipt_field(example_receipt_field.to_item()) == example_receipt_field
    )


@pytest.mark.unit
def test_item_to_receipt_field_missing_keys():
    """item_to_receipt_field raises ValueError when required keys are missing."""
    incomplete_item = {
        "PK": {"S": "FIELD#BUSINESS_NAME"},
        "SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
    }
    with pytest.raises(ValueError, match="Invalid item format\nmissing keys"):
        item_to_receipt_field(incomplete_item)


@pytest.mark.unit
def test_item_to_receipt_field_invalid_format():
    """item_to_receipt_field raises ValueError when keys are incorrectly formatted."""
    invalid_item = {
        "PK": {"S": "FIELD#BUSINESS_NAME"},
        "SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
        "words": {
            "L": [
                {
                    "M": {
                        "word_id": {"N": "1"},
                        "line_id": {"N": "1"},
                        "label": {"S": "BUSINESS_NAME"},
                    }
                }
            ]
        },
        "reasoning": {"N": "123"},  # Should be {"S": "123"}
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
    }
    with pytest.raises(ValueError, match="Error converting item to ReceiptField"):
        item_to_receipt_field(invalid_item)
