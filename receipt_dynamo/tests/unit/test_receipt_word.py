# pylint: disable=redefined-outer-name
"""Unit tests for the ReceiptWord entity."""

from copy import deepcopy

import pytest

from receipt_dynamo import ReceiptWord, item_to_receipt_word

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
BASE_RECEIPT_WORD = {
    "receipt_id": 1,
    "image_id": IMAGE_ID,
    "line_id": 3,
    "word_id": 4,
    "text": "Test",
    "bounding_box": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
    "top_right": {"x": 1.0, "y": 2.0},
    "top_left": {"x": 1.0, "y": 3.0},
    "bottom_right": {"x": 4.0, "y": 2.0},
    "bottom_left": {"x": 1.0, "y": 1.0},
    "angle_degrees": 1.0,
    "angle_radians": 5.0,
    "confidence": 0.9,
}
EXPECTED_DICT = {
    **BASE_RECEIPT_WORD,
    "extracted_data": None,
    "embedding_status": "NONE",
    "is_noise": False,
}


def receipt_word_kwargs(**overrides):
    """Return independent constructor kwargs for a ReceiptWord."""
    kwargs = deepcopy(BASE_RECEIPT_WORD)
    kwargs.update(overrides)
    return kwargs


def make_receipt_word(**overrides):
    """Build a ReceiptWord with optional constructor overrides."""
    return ReceiptWord(**receipt_word_kwargs(**overrides))


@pytest.fixture
def receipt_word_fixture():
    """A pytest fixture for a sample ReceiptWord object."""
    return make_receipt_word()


@pytest.mark.unit
def test_receipt_word_init_valid(receipt_word_fixture):
    """Test that a ReceiptWord with valid arguments initializes correctly."""
    for attr, value in BASE_RECEIPT_WORD.items():
        assert getattr(receipt_word_fixture, attr) == value


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"receipt_id": "1"}, r"^receipt_id must be an integer"),
        ({"receipt_id": -1}, r"^receipt_id must be positive"),
        ({"image_id": 3}, "uuid must be a string"),
        ({"image_id": IMAGE_ID[:-1]}, "uuid must be a valid UUIDv4"),
        ({"line_id": "3"}, r"^line_id must be an integer"),
        ({"line_id": -3}, r"^line_id must be non-negative"),
        ({"word_id": "4"}, r"^word_id must be an integer"),
        ({"word_id": -4}, r"^word_id must be non-negative"),
        ({"text": 1}, r"^text must be a string"),
        (
            {"bounding_box": {"x": 0.1, "y": 0.2, "height": 0.4}},
            "bounding_box must contain the key 'width'",
        ),
        ({"top_right": {"x": 1.0}}, "point must contain the key 'y'"),
        (
            {"angle_degrees": "0.0"},
            "angle_degrees must be float or int, got",
        ),
        (
            {"angle_radians": "0.0"},
            "angle_radians must be float or int, got",
        ),
        ({"confidence": "0.9"}, "confidence must be float or int, got"),
        ({"confidence": 1.1}, "confidence must be between 0 and 1, got"),
        ({"extracted_data": 1}, "extracted_data must be a dict"),
        (
            {"embedding_status": 1},
            "embedding_status must be a string or EmbeddingStatus enum",
        ),
        ({"is_noise": "True"}, "is_noise must be a boolean, got str"),
        ({"is_noise": 1}, "is_noise must be a boolean, got int"),
    ],
)
def test_receipt_word_init_invalid(overrides, match):
    """Test that invalid constructor arguments raise ValueError."""
    with pytest.raises(ValueError, match=match):
        make_receipt_word(**overrides)


@pytest.mark.unit
def test_receipt_word_optional_values():
    """Test optional constructor values are accepted and normalized."""
    assert make_receipt_word(confidence=1).confidence == 1.0
    extracted_data = {"type": "test_type", "value": "test_value"}
    assert make_receipt_word(extracted_data=extracted_data).extracted_data == (
        extracted_data
    )
    assert make_receipt_word().is_noise is False


@pytest.mark.unit
def test_receipt_word_key(receipt_word_fixture):
    """Test that the key() method returns a properly formatted DynamoDB key."""
    assert receipt_word_fixture.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004"},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method_name", "expected"),
    [
        (
            "gsi1_key",
            {
                "GSI1PK": {"S": "EMBEDDING_STATUS#PENDING"},
                "GSI1SK": {
                    "S": (
                        f"WORD#IMAGE#{IMAGE_ID}#"
                        "RECEIPT#00001#LINE#00003#WORD#00004"
                    )
                },
            },
        ),
        (
            "gsi2_key",
            {
                "GSI2PK": {"S": "RECEIPT"},
                "GSI2SK": {
                    "S": (
                        f"IMAGE#{IMAGE_ID}#RECEIPT#00001#"
                        "LINE#00003#WORD#00004"
                    )
                },
            },
        ),
        (
            "gsi3_key",
            {
                "GSI3PK": {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00001"},
                "GSI3SK": {"S": "WORD"},
            },
        ),
    ],
)
def test_receipt_word_gsi_keys(method_name, expected):
    """Test that GSI key methods return properly formatted DynamoDB keys."""
    word = make_receipt_word(
        text="TestWord",
        bounding_box={
            "x": 0.123456789012,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
        },
        top_right={"x": 1.0001, "y": 2.0001},
        top_left={"x": 1.0001, "y": 2.0001},
        bottom_right={"x": 1.0001, "y": 2.0001},
        bottom_left={"x": 1.0001, "y": 2.0001},
        angle_degrees=45.0,
        angle_radians=0.7853981634,
        confidence=0.95,
        embedding_status="PENDING",
    )
    assert getattr(word, method_name)() == expected


@pytest.mark.unit
def test_receipt_word_to_item():
    """Test that to_item() returns a properly formatted DynamoDB item."""
    word = make_receipt_word(
        text="TestWord",
        bounding_box={
            "x": 0.123456789012,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
        },
        top_right={"x": 1.0001, "y": 2.0001},
        top_left={"x": 1.0001, "y": 2.0001},
        bottom_right={"x": 1.0001, "y": 2.0001},
        bottom_left={"x": 1.0001, "y": 2.0001},
        angle_degrees=45.0,
        angle_radians=0.7853981634,
        confidence=0.95,
    )
    item = word.to_item()
    assert item["PK"]["S"] == f"IMAGE#{IMAGE_ID}"
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#WORD#00004"
    assert item["bounding_box"]["M"]["x"]["N"]


@pytest.mark.unit
def test_repr(receipt_word_fixture):
    """Test that the __repr__ method returns a string."""
    assert isinstance(repr(receipt_word_fixture), str)
    assert str(receipt_word_fixture) == repr(receipt_word_fixture)
    assert repr(receipt_word_fixture) == (
        "ReceiptWord("
        "receipt_id=1, "
        f"image_id='{IMAGE_ID}', "
        "line_id=3, "
        "word_id=4, "
        "text='Test', "
        "bounding_box={'x': 0.1, 'y': 0.2, 'width': 0.3, 'height': 0.4}, "
        "top_right={'x': 1.0, 'y': 2.0}, "
        "top_left={'x': 1.0, 'y': 3.0}, "
        "bottom_right={'x': 4.0, 'y': 2.0}, "
        "bottom_left={'x': 1.0, 'y': 1.0}, "
        "angle_degrees=1.0, "
        "angle_radians=5.0, "
        "confidence=0.9, "
        "embedding_status='NONE', "
        "is_noise=False"
        ")"
    )


@pytest.mark.unit
def test_receipt_word_eq(receipt_word_fixture):
    """Test that two ReceiptWords with the same attributes are equal."""
    assert receipt_word_fixture == item_to_receipt_word(
        receipt_word_fixture.to_item()
    )
    assert receipt_word_fixture != "Test"


@pytest.mark.unit
def test_receipt_word_iter(receipt_word_fixture):
    """Test that the __iter__ method returns a complete constructor dict."""
    receipt_word_dict = dict(receipt_word_fixture)
    assert receipt_word_dict == EXPECTED_DICT
    assert ReceiptWord(**receipt_word_dict) == receipt_word_fixture


@pytest.mark.unit
def test_receipt_word_calculate_centroid(receipt_word_fixture):
    """Test that the centroid is calculated correctly."""
    assert receipt_word_fixture.calculate_centroid() == (1.75, 2.0)


@pytest.mark.unit
def test_receipt_word_distance_and_angle(receipt_word_fixture):
    """Test distance and angle calculations against another ReceiptWord."""
    other_receipt_word = make_receipt_word(
        word_id=5,
        top_right={"x": 40.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 40.0, "y": 10.0},
        bottom_left={"x": 10.0, "y": 10.0},
    )
    assert receipt_word_fixture.distance_and_angle_from__receipt_word(
        other_receipt_word
    ) == (26.637614382673235, 0.5098332286596837)


@pytest.mark.unit
def test_item_to_receipt_word_round_trip(receipt_word_fixture):
    """Test that converting an item to ReceiptWord and back is consistent."""
    assert item_to_receipt_word(receipt_word_fixture.to_item()) == (
        receipt_word_fixture
    )
    with pytest.raises(ValueError, match="^Item is missing required keys:"):
        item_to_receipt_word({})
    with pytest.raises(ValueError, match="^Failed to create ReceiptWord:"):
        item = receipt_word_fixture.to_item()
        del item["bounding_box"]["M"]["width"]
        item_to_receipt_word(item)


@pytest.mark.unit
@pytest.mark.parametrize("is_noise", [True, False])
def test_receipt_word_is_noise_serialization(is_noise):
    """Test that is_noise is properly serialized to DynamoDB item."""
    item = make_receipt_word(
        text="." if is_noise else "TOTAL",
        is_noise=is_noise,
    ).to_item()
    assert item["is_noise"]["BOOL"] is is_noise


@pytest.mark.unit
def test_item_to_receipt_word_backward_compatibility(receipt_word_fixture):
    """Test item_to_receipt_word defaults missing is_noise to False."""
    old_item = receipt_word_fixture.to_item()
    del old_item["is_noise"]
    assert item_to_receipt_word(old_item).is_noise is False


@pytest.mark.unit
def test_item_to_receipt_word_with_optional_fields(receipt_word_fixture):
    """Test that item_to_receipt_word handles optional fields."""
    item = receipt_word_fixture.to_item()
    item["is_noise"] = {"BOOL": True}
    item["extracted_data"] = {"M": {"type": {"S": "test"}}}
    word = item_to_receipt_word(item)
    assert word.is_noise is True
    assert word.extracted_data == {"type": "test"}
