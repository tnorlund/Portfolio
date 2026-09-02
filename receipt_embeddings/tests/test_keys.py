"""Canonical vector-item key builders and parsers."""

from receipt_dynamo.entities import (
    EMBEDDING_DIMENSIONS,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)

from receipt_embeddings.keys import (
    canonical_from_dynamo_key,
    canonical_key_from_item,
    dynamo_key_from_canonical,
    embedding_item_key,
    line_canonical_key,
    parse_canonical_key,
    parse_embedding_pk_sk,
    word_canonical_key,
    word_vector_key,
)
from receipt_embeddings.service_limits import LINE_INDEX, WORD_INDEX
from receipt_embeddings.writer import EmbeddingWriteRequest

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def test_line_and_word_builders_match_historical_fstrings() -> None:
    assert (
        line_canonical_key(IMAGE_ID, 1, 2)
        == f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    )
    assert word_canonical_key(IMAGE_ID, 1, 2, 4) == (
        f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002#WORD#00004"
    )
    assert word_vector_key is word_canonical_key


def test_parse_canonical_key_round_trip() -> None:
    line = line_canonical_key(IMAGE_ID, 1, 2)
    word = word_canonical_key(IMAGE_ID, 1, 2, 4)
    parsed_line = parse_canonical_key(line)
    parsed_word = parse_canonical_key(word)
    assert parsed_line is not None and parsed_line.word_id is None
    assert parsed_line.canonical() == line
    assert parsed_word is not None and parsed_word.word_id == 4
    assert parsed_word.canonical() == word
    assert parse_canonical_key("not-a-key") is None


def test_parse_embedding_pk_sk_and_dynamo_key() -> None:
    key = embedding_item_key(IMAGE_ID, 1, 2, 4)
    parsed = parse_embedding_pk_sk(key["PK"]["S"], key["SK"]["S"])
    assert parsed is not None
    assert parsed.dynamo_key() == key
    assert canonical_from_dynamo_key(key) == word_canonical_key(
        IMAGE_ID, 1, 2, 4
    )
    assert parse_embedding_pk_sk("IMAGE#abc", "RECEIPT#1") is None


def test_dynamo_key_from_canonical_matches_backfill_partition() -> None:
    canonical = line_canonical_key(IMAGE_ID, 1, 2)
    assert dynamo_key_from_canonical(canonical) == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001#LINE#00002#EMBEDDING"},
    }


def test_canonical_key_from_item_uses_index() -> None:
    item = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 2,
        "word_id": 4,
    }
    assert canonical_key_from_item(item, index=LINE_INDEX) == (
        line_canonical_key(IMAGE_ID, 1, 2)
    )
    assert canonical_key_from_item(item, index=WORD_INDEX) == (
        word_canonical_key(IMAGE_ID, 1, 2, 4)
    )


def test_entity_and_write_request_keys_agree() -> None:
    vector = [0.01] * EMBEDDING_DIMENSIONS
    line = ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="COFFEE",
        merchant_name="",
        place_id="",
        row_line_ids=[2],
        section_type="",
        line_vector=vector,
    )
    word = ReceiptWordEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=4,
        text="COFFEE",
        merchant_name="",
        label_status="none",
        word_vector=vector,
    )
    line_request = EmbeddingWriteRequest(
        kind="line",
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="COFFEE",
        row_line_ids=(2,),
    )
    word_request = EmbeddingWriteRequest(
        kind="word",
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=4,
        text="COFFEE",
    )
    assert line.key == line_request.key == embedding_item_key(IMAGE_ID, 1, 2)
    assert (
        word.key == word_request.key == embedding_item_key(IMAGE_ID, 1, 2, 4)
    )
    assert line.canonical_key == line_request.canonical_key
    assert word.canonical_key == word_request.canonical_key
