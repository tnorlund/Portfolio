# pylint: disable=redefined-outer-name
import pytest

from receipt_dynamo.entities.receipt_line_embedding import (
    EMBEDDING_DIMENSIONS,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    VALID_LABEL_STATUSES,
    ReceiptWordEmbedding,
    item_to_receipt_word_embedding,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def make_vector(seed: float = 0.5) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = seed
    return vector


@pytest.fixture
def example_word_embedding():
    return ReceiptWordEmbedding(
        receipt_id=7,
        image_id=IMAGE_ID,
        line_id=3,
        word_id=2,
        word_vector=make_vector(),
        text="TOTAL",
        label_status="validated",
        merchant_name="Costco",
        primary_label="GRAND_TOTAL",
        valid_labels=["GRAND_TOTAL"],
    )


@pytest.mark.unit
def test_word_embedding_init_valid(example_word_embedding):
    assert example_word_embedding.word_id == 2
    assert example_word_embedding.label_status == "validated"
    assert len(example_word_embedding.word_vector) == EMBEDDING_DIMENSIONS


@pytest.mark.unit
def test_word_embedding_key_shape(example_word_embedding):
    assert example_word_embedding.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00007#LINE#00003#WORD#00002#EMBEDDING"},
    }


@pytest.mark.unit
def test_word_embedding_to_item_contract(example_word_embedding):
    item = example_word_embedding.to_item()
    assert item["TYPE"] == {"S": "RECEIPT_WORD_EMBEDDING"}
    # Vector attribute name is fixed by the live word-embeddings index.
    assert len(item["word_vector"]["L"]) == EMBEDDING_DIMENSIONS
    # Projection attributes of the word-embeddings index.
    assert item["text"] == {"S": "TOTAL"}
    assert item["merchant_name"] == {"S": "Costco"}
    assert item["image_id"] == {"S": IMAGE_ID}
    assert item["receipt_id"] == {"N": "7"}
    assert item["line_id"] == {"N": "3"}
    assert item["word_id"] == {"N": "2"}
    assert item["label_status"] == {"S": "validated"}


@pytest.mark.unit
def test_word_embedding_has_no_gsi_keys(example_word_embedding):
    """Embedding items must stay out of GSI1-4 and GSI2/3 access paths."""
    item = example_word_embedding.to_item()
    forbidden = {
        key
        for key in item
        if key.startswith("GSI") and key not in ("PK", "SK")
    }
    assert forbidden == set()


@pytest.mark.unit
def test_word_embedding_round_trip(example_word_embedding):
    item = example_word_embedding.to_item()
    assert item_to_receipt_word_embedding(item) == example_word_embedding


@pytest.mark.unit
def test_word_embedding_optional_fields_absent():
    embedding = ReceiptWordEmbedding(
        receipt_id=1,
        image_id=IMAGE_ID,
        line_id=0,
        word_id=0,
        word_vector=make_vector(),
        text="hello",
    )
    assert embedding.label_status == "none"
    item = embedding.to_item()
    assert "merchant_name" not in item
    assert "primary_label" not in item
    assert "valid_labels" not in item
    assert item_to_receipt_word_embedding(item) == embedding


@pytest.mark.unit
def test_word_embedding_label_statuses_are_closed_set():
    assert set(VALID_LABEL_STATUSES) == {"validated", "pending", "none"}
    for status in VALID_LABEL_STATUSES:
        ReceiptWordEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            word_id=1,
            word_vector=make_vector(),
            text="hello",
            label_status=status,
        )
    with pytest.raises(ValueError, match="label_status"):
        ReceiptWordEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            word_id=1,
            word_vector=make_vector(),
            text="hello",
            label_status="auto_suggested",
        )


@pytest.mark.unit
def test_word_embedding_invalid_vector():
    with pytest.raises(ValueError, match="word_vector"):
        ReceiptWordEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            word_id=1,
            word_vector=[0.5] * 12,
            text="hello",
        )


@pytest.mark.unit
def test_word_embedding_duplicate_valid_labels():
    with pytest.raises(ValueError, match="valid_labels"):
        ReceiptWordEmbedding(
            receipt_id=1,
            image_id=IMAGE_ID,
            line_id=1,
            word_id=1,
            word_vector=make_vector(),
            text="hello",
            valid_labels=["TAX", "TAX"],
        )


@pytest.mark.unit
def test_word_embedding_from_item_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        ReceiptWordEmbedding.from_item({"PK": {"S": f"IMAGE#{IMAGE_ID}"}})
