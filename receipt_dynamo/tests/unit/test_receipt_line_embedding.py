# pylint: disable=redefined-outer-name
import pytest

from receipt_dynamo.entities.receipt_line_embedding import (
    EMBEDDING_DIMENSIONS,
    ReceiptLineEmbedding,
    embedding_number_string,
    item_to_receipt_line_embedding,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def make_vector(seed: float = 0.25) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = seed
    vector[-1] = 6.6e-05
    return vector


@pytest.fixture
def example_line_embedding():
    return ReceiptLineEmbedding(
        receipt_id=7,
        image_id=IMAGE_ID,
        line_id=3,
        line_vector=make_vector(),
        text="COSTCO WHOLESALE 5.99",
        row_line_ids=[3, 4],
        merchant_name="Costco",
        place_id="ChIJexample",
        section_type="HEADER",
    )


@pytest.mark.unit
def test_line_embedding_init_valid(example_line_embedding):
    assert example_line_embedding.receipt_id == 7
    assert example_line_embedding.line_id == 3
    assert len(example_line_embedding.line_vector) == EMBEDDING_DIMENSIONS
    assert example_line_embedding.row_line_ids == [3, 4]


@pytest.mark.unit
def test_line_embedding_key_shape(example_line_embedding):
    assert example_line_embedding.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00007#LINE#00003#EMBEDDING"},
    }


@pytest.mark.unit
def test_line_embedding_to_item_contract(example_line_embedding):
    item = example_line_embedding.to_item()
    assert item["TYPE"] == {"S": "RECEIPT_LINE_EMBEDDING"}
    # Vector attribute name is fixed by the live line-embeddings index.
    assert len(item["line_vector"]["L"]) == EMBEDDING_DIMENSIONS
    # Projection attributes of the line-embeddings index.
    assert item["text"] == {"S": "COSTCO WHOLESALE 5.99"}
    assert item["merchant_name"] == {"S": "Costco"}
    assert item["place_id"] == {"S": "ChIJexample"}
    assert item["image_id"] == {"S": IMAGE_ID}
    assert item["receipt_id"] == {"N": "7"}
    assert item["line_id"] == {"N": "3"}
    assert item["row_line_ids"] == {"L": [{"N": "3"}, {"N": "4"}]}
    assert item["section_type"] == {"S": "HEADER"}


@pytest.mark.unit
def test_line_embedding_has_no_gsi_keys(example_line_embedding):
    """Embedding items must stay out of GSI1-4 and GSI2/3 access paths."""
    item = example_line_embedding.to_item()
    forbidden = {
        key
        for key in item
        if key.startswith("GSI") and key not in ("PK", "SK")
    }
    assert forbidden == set()


@pytest.mark.unit
def test_line_embedding_round_trip(example_line_embedding):
    item = example_line_embedding.to_item()
    assert item_to_receipt_line_embedding(item) == example_line_embedding


@pytest.mark.unit
def test_line_embedding_optional_fields_absent():
    embedding = ReceiptLineEmbedding(
        receipt_id=1,
        image_id=IMAGE_ID,
        line_id=0,
        line_vector=make_vector(),
        text="hello",
        row_line_ids=[0],
    )
    item = embedding.to_item()
    assert "merchant_name" not in item
    assert "place_id" not in item
    assert "section_type" not in item
    assert item_to_receipt_line_embedding(item) == embedding


@pytest.mark.unit
def test_line_embedding_number_string_is_positional():
    """DynamoDB Number strings must avoid scientific notation."""
    serialized = embedding_number_string(6.6e-05)
    assert "e" not in serialized.lower()
    assert float(serialized) == 6.6e-05


@pytest.mark.unit
@pytest.mark.parametrize(
    "vector",
    [
        [0.1] * 10,  # wrong dimension
        [0.0] * EMBEDDING_DIMENSIONS,  # zero vector
        [float("nan")] * EMBEDDING_DIMENSIONS,  # not finite
        ["x"] * EMBEDDING_DIMENSIONS,  # not numeric
        "not-a-list",
    ],
)
def test_line_embedding_invalid_vector(vector):
    with pytest.raises(ValueError, match="line_vector"):
        ReceiptLineEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            line_vector=vector,
            text="hello",
            row_line_ids=[1],
        )


@pytest.mark.unit
def test_line_embedding_row_line_ids_must_include_line_id():
    with pytest.raises(ValueError, match="row_line_ids must include line_id"):
        ReceiptLineEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            line_vector=make_vector(),
            text="hello",
            row_line_ids=[2, 3],
        )


@pytest.mark.unit
def test_line_embedding_invalid_section_type():
    with pytest.raises(ValueError, match="section_type"):
        ReceiptLineEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            line_vector=make_vector(),
            text="hello",
            row_line_ids=[1],
            section_type="NOT_A_SECTION",
        )


@pytest.mark.unit
def test_line_embedding_invalid_text():
    with pytest.raises(ValueError, match="text"):
        ReceiptLineEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            line_vector=make_vector(),
            text="",
            row_line_ids=[1],
        )


@pytest.mark.unit
def test_line_embedding_from_item_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        ReceiptLineEmbedding.from_item({"PK": {"S": f"IMAGE#{IMAGE_ID}"}})
