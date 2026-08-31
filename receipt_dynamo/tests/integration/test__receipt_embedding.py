"""Moto-backed CRUD tests for the embedding-item accessors.

SearchVectors itself cannot be mocked (moto does not support vector
indexes); these tests cover the plain-DynamoDB accessor surface the
embed-and-put writer and backfill script rely on.
"""

from typing import Literal

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from receipt_dynamo.entities.receipt_line_embedding import (
    EMBEDDING_DIMENSIONS,
    ReceiptLineEmbedding,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

pytestmark = [pytest.mark.integration]


def make_vector(seed: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = seed
    vector[-1] = 6.6e-05
    return vector


def make_line_embedding(line_id: int) -> ReceiptLineEmbedding:
    return ReceiptLineEmbedding(
        receipt_id=1,
        image_id=IMAGE_ID,
        line_id=line_id,
        line_vector=make_vector(0.1 + line_id),
        text=f"row {line_id}",
        row_line_ids=[line_id],
        merchant_name="Costco",
        section_type="HEADER",
    )


def make_word_embedding(line_id: int, word_id: int) -> ReceiptWordEmbedding:
    return ReceiptWordEmbedding(
        receipt_id=1,
        image_id=IMAGE_ID,
        line_id=line_id,
        word_id=word_id,
        word_vector=make_vector(0.2 + word_id),
        text=f"word {word_id}",
        label_status="none",
    )


def test_add_and_get_receipt_line_embedding(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    embedding = make_line_embedding(3)

    client.add_receipt_line_embedding(embedding)

    retrieved = client.get_receipt_line_embedding(IMAGE_ID, 1, 3)
    assert retrieved == embedding


def test_add_receipt_line_embedding_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    embedding = make_line_embedding(3)
    client.add_receipt_line_embedding(embedding)
    with pytest.raises(EntityAlreadyExistsError):
        client.add_receipt_line_embedding(embedding)


def test_get_receipt_line_embedding_not_found(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_line_embedding(IMAGE_ID, 1, 99)


def test_list_receipt_line_embeddings_from_receipt(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    line_embeddings = [make_line_embedding(i) for i in (1, 2, 5)]
    client.add_receipt_line_embeddings(line_embeddings)
    # Word embeddings share the SK prefix; the TYPE filter must exclude
    # them from the line listing (and vice versa).
    client.add_receipt_word_embeddings([make_word_embedding(1, 1)])

    listed = client.list_receipt_line_embeddings_from_receipt(IMAGE_ID, 1)
    assert sorted(e.line_id for e in listed) == [1, 2, 5]


def test_add_and_get_receipt_word_embedding(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    embedding = make_word_embedding(3, 7)

    client.add_receipt_word_embedding(embedding)

    retrieved = client.get_receipt_word_embedding(IMAGE_ID, 1, 3, 7)
    assert retrieved == embedding


def test_get_receipt_word_embedding_not_found(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_word_embedding(IMAGE_ID, 1, 3, 99)


def test_list_receipt_word_embeddings_from_receipt(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    word_embeddings = [
        make_word_embedding(1, 1),
        make_word_embedding(1, 2),
        make_word_embedding(2, 1),
    ]
    client.add_receipt_word_embeddings(word_embeddings)
    client.add_receipt_line_embeddings([make_line_embedding(1)])

    listed = client.list_receipt_word_embeddings_from_receipt(IMAGE_ID, 1)
    assert sorted((e.line_id, e.word_id) for e in listed) == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]


def test_delete_receipt_embeddings(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    line_embedding = make_line_embedding(1)
    word_embedding = make_word_embedding(1, 1)
    client.add_receipt_line_embeddings([line_embedding])
    client.add_receipt_word_embeddings([word_embedding])

    client.delete_receipt_line_embeddings([line_embedding])
    client.delete_receipt_word_embeddings([word_embedding])

    assert client.list_receipt_line_embeddings_from_receipt(IMAGE_ID, 1) == []
    assert client.list_receipt_word_embeddings_from_receipt(IMAGE_ID, 1) == []


def test_list_receipt_embeddings_by_type(
    dynamodb_table: Literal["MyMockedTable"],
):
    """TYPE stays enumerable for backfill audits (spec §3.1)."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line_embeddings([make_line_embedding(1)])
    client.add_receipt_word_embeddings([make_word_embedding(1, 1)])

    lines, _ = client.list_receipt_line_embeddings()
    words, _ = client.list_receipt_word_embeddings()
    assert len(lines) == 1
    assert len(words) == 1
