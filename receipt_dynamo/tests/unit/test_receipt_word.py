# pylint: disable=redefined-outer-name
import pytest

from receipt_dynamo import ReceiptWord, item_to_receipt_word


@pytest.fixture
def receipt_word_fixture():
    """A pytest fixture for a sample ReceiptWord object."""
    return ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="Test",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 3.0},
        bottom_right={"x": 4.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 1.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
    )


@pytest.mark.unit
def test_receipt_word_init_valid(
    receipt_word_fixture,
):
    """Test that a ReceiptWord with valid arguments initializes correctly."""
    assert receipt_word_fixture.receipt_id == 1
    assert receipt_word_fixture.image_id == (
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert receipt_word_fixture.line_id == 3
    assert receipt_word_fixture.word_id == 4
    assert receipt_word_fixture.text == "Test"
    assert receipt_word_fixture.bounding_box == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.4,
    }
    assert receipt_word_fixture.top_right == {"x": 1.0, "y": 2.0}
    assert receipt_word_fixture.top_left == {"x": 1.0, "y": 3.0}
    assert receipt_word_fixture.bottom_right == {"x": 4.0, "y": 2.0}
    assert receipt_word_fixture.bottom_left == {"x": 1.0, "y": 1.0}
    assert receipt_word_fixture.angle_degrees == 1.0
    assert receipt_word_fixture.angle_radians == 5.0
    assert receipt_word_fixture.confidence == 0.9


@pytest.mark.unit
def test_receipt_word_init_invalid_receipt_id():
    """Test that invalid receipt_id raises ValueError."""
    with pytest.raises(ValueError, match="^receipt_id must be an integer"):
        ReceiptWord(
            receipt_id="1",  # Not an integer
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="^receipt_id must be positive"):
        ReceiptWord(
            receipt_id=-1,  # Negative
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_uuid():
    """Test that invalid UUIDs raise ValueError."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptWord(
            receipt_id=1,
            image_id=3,  # Not a string
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUIDv4"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_line_id():
    """Test that invalid line_id raises ValueError."""
    with pytest.raises(ValueError, match="^line_id must be an integer"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id="3",  # Not an integer
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="^line_id must be positive"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=-3,  # Negative
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_id():
    """Test that invalid word_id raises ValueError."""
    with pytest.raises(ValueError, match="^id must be an integer"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id="4",  # Not an integer
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="^id must be positive"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=-4,  # Negative
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_text():
    """Test that invalid text raises ValueError."""
    with pytest.raises(ValueError, match="^text must be a string"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text=1,  # Not a string
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_bounding_box():
    """Test that invalid bounding_box keys or types raise ValueError."""
    with pytest.raises(
        ValueError,
        match="bounding_box must contain the key 'width'",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "height": 0.4},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_corners():
    """Test that invalid point keys or types raise ValueError."""
    with pytest.raises(ValueError, match="point must contain the key 'y'"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 1.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_angle_validation():
    """Test that angles outside [0, 360) raise ValueError."""
    with pytest.raises(
        ValueError,
        match="angle_degrees must be float or int, got",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees="0.0",
            angle_radians=0.0,
            confidence=0.9,
        )
    with pytest.raises(
        ValueError,
        match="angle_radians must be float or int, got",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians="0.0",
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_confidence():
    """Test that confidence outside (0,1] raises ValueError."""
    with pytest.raises(
        ValueError, match="confidence must be float or int, got"
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence="0.9",
        )
    receipt = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=1,
        text="Test",
        bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 2.0},
        bottom_right={"x": 1.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 2.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1,
    )
    assert receipt.confidence == 1.0
    with pytest.raises(
        ValueError, match="confidence must be between 0 and 1, got"
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.1,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_extracted_data():
    """Test that invalid extracted_data raises ValueError."""
    with pytest.raises(ValueError, match="extracted_data must be a dict"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
            extracted_data=1,
        )
    receipt = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=1,
        text="Test",
        bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 2.0},
        bottom_right={"x": 1.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 2.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.9,
        extracted_data={"type": "test_type", "value": "test_value"},
    )
    assert receipt.extracted_data == {
        "type": "test_type",
        "value": "test_value",
    }


@pytest.mark.unit
def test_receipt_word_init_invalid_embedding_status():
    """Test that invalid embedding_status raises ValueError."""
    with pytest.raises(
        ValueError,
        match="embedding_status must be a string or EmbeddingStatus enum",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
            embedding_status=1,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_is_noise():
    """Test that is_noise must be a boolean."""
    with pytest.raises(
        ValueError,
        match="is_noise must be a boolean, got str",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
            is_noise="True",  # Should be boolean, not string
        )

    # Test that integer values also raise error
    with pytest.raises(
        ValueError,
        match="is_noise must be a boolean, got int",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
            is_noise=1,  # Should be boolean, not int
        )


@pytest.mark.unit
def test_receipt_word_key(receipt_word_fixture):
    """Test that the key() method returns a properly formatted DynamoDB key."""
    assert receipt_word_fixture.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004"},
    }


@pytest.mark.unit
def test_receipt_word_gsi1_key():
    """Test that the gsi1_key() method returns a properly
    formatted DynamoDB key."""
    word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
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
    assert word.gsi1_key() == {
        "GSI1PK": {"S": "EMBEDDING_STATUS#PENDING"},
        "GSI1SK": {
            "S": (
                "WORD#IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#"
                "RECEIPT#00001#LINE#00003#WORD#00004"
            )
        },
    }


@pytest.mark.unit
def test_receipt_word_gsi2_key():
    """Test that the gsi2_key() method returns a properly
    formatted DynamoDB key."""
    word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
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
    assert word.gsi2_key() == {
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {
            "S": (
                "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#"
                "LINE#00003#WORD#00004"
            )
        },
    }


@pytest.mark.unit
def test_receipt_word_gsi3_key():
    """Test that the gsi3_key() method returns a properly
    formatted DynamoDB key."""
    word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
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
    assert word.gsi3_key() == {
        "GSI3PK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"
        },
        "GSI3SK": {"S": "WORD"},
    }


@pytest.mark.unit
def test_receipt_word_to_item():
    """Test that to_item() returns a properly formatted DynamoDB item."""
    bounding_box = {"x": 0.123456789012, "y": 0.2, "width": 0.3, "height": 0.4}
    point = {"x": 1.0001, "y": 2.0001}

    word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="TestWord",
        bounding_box=bounding_box,
        top_right=point,
        top_left=point,
        bottom_right=point,
        bottom_left=point,
        angle_degrees=45.0,
        angle_radians=0.7853981634,
        confidence=0.95,
    )
    item = word.to_item()
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#WORD#00004"
    assert item["bounding_box"]["M"]["x"]["N"]


@pytest.mark.unit
def test_repr(receipt_word_fixture):
    """Test that the __repr__ method returns a string."""
    assert isinstance(repr(receipt_word_fixture), str)
    assert str(receipt_word_fixture) == repr(receipt_word_fixture)
    expected_repr = (
        "ReceiptWord("
        "receipt_id=1, "
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
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
    assert repr(receipt_word_fixture) == expected_repr


@pytest.mark.unit
def test_receipt_word_eq(receipt_word_fixture):
    """Test that two ReceiptWords with the same attributes are equal."""
    word1 = receipt_word_fixture
    word2 = item_to_receipt_word(word1.to_item())
    assert word1 == word2
    assert word1 != "Test"


@pytest.mark.unit
def test_receipt_word_iter(receipt_word_fixture):
    """Test that the __iter__ method returns a dictionary."""
    receipt_word_dict = dict(receipt_word_fixture)
    expected_keys = {
        "receipt_id",
        "image_id",
        "line_id",
        "word_id",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
        "extracted_data",
        "embedding_status",
        "is_noise",
    }
    assert set(receipt_word_dict.keys()) == expected_keys
    assert receipt_word_dict["receipt_id"] == 1
    assert receipt_word_dict["image_id"] == (
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert receipt_word_dict["line_id"] == 3
    assert receipt_word_dict["word_id"] == 4
    assert receipt_word_dict["text"] == "Test"
    assert receipt_word_dict["bounding_box"] == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.4,
    }
    assert receipt_word_dict["top_right"] == {"x": 1.0, "y": 2.0}
    assert receipt_word_dict["top_left"] == {"x": 1.0, "y": 3.0}
    assert receipt_word_dict["bottom_right"] == {"x": 4.0, "y": 2.0}
    assert receipt_word_dict["bottom_left"] == {"x": 1.0, "y": 1.0}
    assert receipt_word_dict["angle_degrees"] == 1.0
    assert receipt_word_dict["angle_radians"] == 5.0
    assert receipt_word_dict["confidence"] == 0.9
    assert receipt_word_dict["embedding_status"] == "NONE"
    assert receipt_word_dict["is_noise"] is False
    assert ReceiptWord(**receipt_word_dict) == receipt_word_fixture


@pytest.mark.unit
def test_receipt_word_calculate_centroid(receipt_word_fixture):
    """Test that the centroid is calculated correctly."""
    assert receipt_word_fixture.calculate_centroid() == (1.75, 2.0)


@pytest.mark.unit
def test_receipt_word_distance_and_angle(receipt_word_fixture):
    other_receipt_word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=5,
        text="Test",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_right={"x": 40.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 40.0, "y": 10.0},
        bottom_left={"x": 10.0, "y": 10.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
    )
    result = receipt_word_fixture.distance_and_angle_from__receipt_word(
        other_receipt_word
    )
    expected = (26.637614382673235, 0.5098332286596837)
    assert result == expected


@pytest.mark.unit
def test_item_to_receipt_word_round_trip(receipt_word_fixture):
    """Test that converting an item to ReceiptWord and back is consistent."""
    assert (
        item_to_receipt_word(receipt_word_fixture.to_item())
        == receipt_word_fixture
    )
    with pytest.raises(ValueError, match="^Item is missing required keys:"):
        item_to_receipt_word({})
    with pytest.raises(
        ValueError,
        match="^Failed to create ReceiptWord:",
    ):
        item_to_receipt_word(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004"},
                "text": {"S": "Test"},
                "bounding_box": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}},
                "top_right": {"M": {"x": {"N": "1.0"}, "y": {"N": "2.0"}}},
                "top_left": {"M": {"x": {"N": "1.0"}, "y": {"N": "3.0"}}},
                "bottom_right": {"M": {"x": {"N": "4.0"}, "y": {"N": "2.0"}}},
                "bottom_left": {"M": {"x": {"N": "1.0"}, "y": {"N": "1.0"}}},
                "angle_degrees": {"N": "1.0"},
                "angle_radians": {"N": "5.0"},
                "confidence": {"N": "0.9"},
            }
        )


@pytest.mark.unit
def test_receipt_word_is_noise_default():
    """Test that is_noise defaults to False."""
    word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="Test",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 3.0},
        bottom_right={"x": 4.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 1.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
        # is_noise not specified, should default to False
    )
    assert word.is_noise is False


@pytest.mark.unit
def test_receipt_word_is_noise_serialization():
    """Test that is_noise is properly serialized to DynamoDB item."""
    # Test with is_noise=True
    word_noise = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text=".",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 3.0},
        bottom_right={"x": 4.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 1.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
        is_noise=True,
    )
    item = word_noise.to_item()
    assert "is_noise" in item
    assert item["is_noise"]["BOOL"] is True

    # Test with is_noise=False
    word_not_noise = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="TOTAL",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 3.0},
        bottom_right={"x": 4.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 1.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
        is_noise=False,
    )
    item = word_not_noise.to_item()
    assert "is_noise" in item
    assert item["is_noise"]["BOOL"] is False


@pytest.mark.unit
def test_item_to_receipt_word_backward_compatibility():
    """Test that item_to_receipt_word handles missing is_noise field
    for backward compatibility."""
    # Old item without is_noise field
    old_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004"},
        "text": {"S": "Test"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.1"},
                "y": {"N": "0.2"},
                "width": {"N": "0.3"},
                "height": {"N": "0.4"},
            }
        },
        "top_right": {"M": {"x": {"N": "1.0"}, "y": {"N": "2.0"}}},
        "top_left": {"M": {"x": {"N": "1.0"}, "y": {"N": "3.0"}}},
        "bottom_right": {"M": {"x": {"N": "4.0"}, "y": {"N": "2.0"}}},
        "bottom_left": {"M": {"x": {"N": "1.0"}, "y": {"N": "1.0"}}},
        "angle_degrees": {"N": "1.0"},
        "angle_radians": {"N": "5.0"},
        "confidence": {"N": "0.9"},
        "embedding_status": {"S": "NONE"},
        # No is_noise field
    }

    word = item_to_receipt_word(old_item)
    assert word.is_noise is False  # Should default to False


@pytest.mark.unit
def test_item_to_receipt_word_with_optional_fields(receipt_word_fixture):
    """Test that item_to_receipt_word handles optional fields."""
    item = receipt_word_fixture.to_item()
    item["is_noise"] = {"BOOL": True}
    item["extracted_data"] = {"M": {"type": {"S": "test"}}}
    word = item_to_receipt_word(item)
    assert word.is_noise is True
    assert word.extracted_data == {"type": "test"}
