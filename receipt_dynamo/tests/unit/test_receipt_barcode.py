# pylint: disable=redefined-outer-name
import pytest

from receipt_dynamo import ReceiptBarcode, item_to_receipt_barcode

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


@pytest.fixture
def receipt_barcode_fixture():
    """A sample ReceiptBarcode (decoded Code128)."""
    return ReceiptBarcode(
        image_id=IMAGE_ID,
        receipt_id=1,
        barcode_id=0,
        symbology="Code128",
        text="030141189531070",
        bounding_box={"x": 0.1, "y": 0.32, "width": 0.78, "height": 0.06},
        top_right={"x": 0.88, "y": 0.38},
        top_left={"x": 0.1, "y": 0.38},
        bottom_right={"x": 0.88, "y": 0.32},
        bottom_left={"x": 0.1, "y": 0.32},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.93,
    )


@pytest.mark.unit
def test_receipt_barcode_init_valid(receipt_barcode_fixture):
    b = receipt_barcode_fixture
    assert b.image_id == IMAGE_ID
    assert b.receipt_id == 1
    assert b.barcode_id == 0
    assert b.symbology == "Code128"
    assert b.text == "030141189531070"
    assert b.payload == "030141189531070"
    assert b.confidence == 0.93


@pytest.mark.unit
def test_receipt_barcode_key_and_type(receipt_barcode_fixture):
    item = receipt_barcode_fixture.to_item()
    assert item["PK"]["S"] == f"IMAGE#{IMAGE_ID}"
    assert item["SK"]["S"] == "RECEIPT#00001#BARCODE#00000"
    assert item["TYPE"]["S"] == "RECEIPT_BARCODE"
    assert item["GSI3PK"]["S"] == f"IMAGE#{IMAGE_ID}#RECEIPT#00001"
    assert item["GSI3SK"]["S"] == "BARCODE"
    assert item["GSI4PK"]["S"] == f"IMAGE#{IMAGE_ID}#RECEIPT#00001"
    assert item["GSI4SK"]["S"] == "6_BARCODE#00000"
    assert item["symbology"]["S"] == "Code128"
    assert item["text"]["S"] == "030141189531070"


@pytest.mark.unit
def test_receipt_barcode_roundtrip(receipt_barcode_fixture):
    item = receipt_barcode_fixture.to_item()
    restored = item_to_receipt_barcode(item)
    assert restored == receipt_barcode_fixture
    assert restored.to_item() == item


@pytest.mark.unit
def test_receipt_barcode_undecoded_payload_is_none():
    """A located-but-undecoded symbol stores '' and exposes payload None."""
    b = ReceiptBarcode(
        image_id=IMAGE_ID,
        receipt_id=2,
        barcode_id=1,
        symbology="QR",
        text="",
        bounding_box={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        top_right={"x": 0.3, "y": 0.3},
        top_left={"x": 0.1, "y": 0.3},
        bottom_right={"x": 0.3, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.1},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.5,
    )
    assert b.payload is None
    assert item_to_receipt_barcode(b.to_item()).payload is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,match",
    [
        ("receipt_id", "1", "receipt_id must be an integer"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("barcode_id", "0", "barcode_id must be an integer"),
        ("barcode_id", -1, "barcode_id must be non-negative"),
        ("symbology", "", "symbology must be a non-empty string"),
        ("symbology", None, "symbology must be a non-empty string"),
    ],
)
def test_receipt_barcode_invalid_fields(field, value, match):
    kwargs = dict(
        image_id=IMAGE_ID,
        receipt_id=1,
        barcode_id=0,
        symbology="Code128",
        text="x",
        bounding_box={"x": 0.1, "y": 0.32, "width": 0.78, "height": 0.06},
        top_right={"x": 0.88, "y": 0.38},
        top_left={"x": 0.1, "y": 0.38},
        bottom_right={"x": 0.88, "y": 0.32},
        bottom_left={"x": 0.1, "y": 0.32},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.93,
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=match):
        ReceiptBarcode(**kwargs)


@pytest.mark.unit
def test_receipt_barcode_invalid_image_id():
    with pytest.raises((ValueError, AssertionError)):
        ReceiptBarcode(
            image_id="not-a-uuid",
            receipt_id=1,
            barcode_id=0,
            symbology="Code128",
            text="x",
            bounding_box={"x": 0.1, "y": 0.32, "width": 0.78, "height": 0.06},
            top_right={"x": 0.88, "y": 0.38},
            top_left={"x": 0.1, "y": 0.38},
            bottom_right={"x": 0.88, "y": 0.32},
            bottom_left={"x": 0.1, "y": 0.32},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.93,
        )


@pytest.mark.unit
def test_receipt_barcode_from_item_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        item_to_receipt_barcode({"PK": {"S": f"IMAGE#{IMAGE_ID}"}})
