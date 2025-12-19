import pytest

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.embedding_batch_result import (
    EmbeddingBatchResult,
    item_to_embedding_batch_result,
)


@pytest.fixture
def example_embedding_batch_result():
    return EmbeddingBatchResult(
        batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
        image_id="1d5ab3e0-7d81-4de4-b23d-1f490f85d89c",
        receipt_id=1,
        line_id=27,
        word_id=3,
        pinecone_id="IMAGE#1d5ab3e0-7d81-4de4-b23d-1f490f85d89c#RECEIPT#00001#LINE#00027#WORD#00003",
        status=EmbeddingStatus.SUCCESS.value,
        text="Organic Bananas",
        error_message=None,
    )


# === BASIC CONSTRUCTION ===


@pytest.mark.unit
def test_embedding_batch_result_valid(example_embedding_batch_result):
    assert (
        example_embedding_batch_result.batch_id
        == "dc7e61ba-5722-43a2-8e99-9df9f54287a9"
    )


@pytest.mark.unit
def test_embedding_batch_result_to_item_and_back(
    example_embedding_batch_result,
):
    item = example_embedding_batch_result.to_item()
    restored = item_to_embedding_batch_result(item)
    assert restored == example_embedding_batch_result


# === REPRESENTATION ===


@pytest.mark.unit
def test_embedding_batch_result_repr(example_embedding_batch_result):
    s = repr(example_embedding_batch_result)
    assert "EmbeddingBatchResult(" in s
    assert f"status='SUCCESS'" in s
    assert f"receipt_id='1'" in s
    assert f"line_id='27'" in s
    assert f"word_id='3'" in s


@pytest.mark.unit
def test_embedding_batch_result_str(example_embedding_batch_result):
    assert str(example_embedding_batch_result) == repr(
        example_embedding_batch_result
    )


# === ITERATION ===


@pytest.mark.unit
def test_embedding_batch_result_iter(example_embedding_batch_result):
    result_dict = dict(example_embedding_batch_result)
    expected_keys = {
        "batch_id",
        "image_id",
        "receipt_id",
        "line_id",
        "word_id",
        "pinecone_id",
        "text",
        "error_message",
        "status",
    }
    assert set(result_dict.keys()) == expected_keys
    assert result_dict["receipt_id"] == 1
    assert result_dict["status"] == EmbeddingStatus.SUCCESS.value


# === FIELD VALIDATION ===


@pytest.mark.unit
@pytest.mark.parametrize(
    "field, value, expected_error",
    [
        ("receipt_id", "not-an-int", "receipt_id must be int, got str"),
        ("line_id", "nope", "line_id must be int, got str"),
        ("word_id", "fail", "word_id must be int, got str"),
        ("pinecone_id", "invalid", "pinecone_id must be in the format"),
        (
            "status",
            123,
            "EmbeddingStatus must be a str or EmbeddingStatus instance",
        ),
        ("text", 456, "text must be str, got int"),
        ("error_message", 999, "error_message must be str, got int"),
    ],
)
def test_embedding_batch_result_invalid_field(field, value, expected_error):
    kwargs = dict(
        batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=101,
        line_id=2,
        word_id=3,
        pinecone_id="WORD#RECEIPT#00101#LINE#00002#WORD#00003#LABEL#ITEM",
        status=EmbeddingStatus.SUCCESS.value,
        text="OK",
        error_message=None,
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=expected_error):
        EmbeddingBatchResult(**kwargs)


@pytest.mark.unit
def test_receipt_id_must_be_positive():
    with pytest.raises(
        ValueError, match="receipt_id must be greater than zero"
    ):
        EmbeddingBatchResult(
            batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=0,
            line_id=1,
            word_id=1,
            pinecone_id="WORD#RECEIPT#00000#LINE#00001#WORD#00001#LABEL#ITEM",
            status=EmbeddingStatus.SUCCESS.value,
            text="txt",
            error_message=None,
        )


@pytest.mark.unit
def test_line_id_must_be_positive():
    with pytest.raises(
        ValueError, match="line_id must be greater than or equal to zero"
    ):
        EmbeddingBatchResult(
            batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=-1,
            word_id=1,
            pinecone_id="WORD#RECEIPT#00001#LINE#-00001#WORD#00001#LABEL#ITEM",
            status=EmbeddingStatus.SUCCESS.value,
            text="txt",
            error_message=None,
        )


@pytest.mark.unit
def test_word_id_must_be_positive():
    with pytest.raises(
        ValueError, match="word_id must be greater than or equal to zero"
    ):
        EmbeddingBatchResult(
            batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=-1,
            pinecone_id="WORD#RECEIPT#00001#LINE#00001#WORD#-00001#LABEL#ITEM",
            status=EmbeddingStatus.SUCCESS.value,
            text="txt",
            error_message=None,
        )


@pytest.mark.unit
def test_invalid_embedding_status_enum():
    with pytest.raises(ValueError, match="EmbeddingStatus must be one of"):
        EmbeddingBatchResult(
            batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=1,
            pinecone_id="WORD#RECEIPT#00101#LINE#00002#WORD#00003#LABEL#ITEM",
            status="BAD",
            text="txt",
            error_message=None,
        )


@pytest.mark.unit
def test_pinecone_id_must_be_in_expected_format():
    with pytest.raises(ValueError, match="pinecone_id must be in the format"):
        EmbeddingBatchResult(
            batch_id="dc7e61ba-5722-43a2-8e99-9df9f54287a9",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=1,
            word_id=1,
            pinecone_id="BAD",
            status=EmbeddingStatus.SUCCESS.value,
            text="txt",
            error_message=None,
        )


# === EQUALITY & HASHING ===


@pytest.mark.unit
def test_embedding_batch_result_equality_diff_status(
    example_embedding_batch_result,
):
    changed = EmbeddingBatchResult(
        **{
            **dict(example_embedding_batch_result),
            "status": EmbeddingStatus.FAILED.value,
        }
    )
    assert example_embedding_batch_result != changed
    assert example_embedding_batch_result != "not-a-result"


@pytest.mark.unit
def test_embedding_batch_result_hash_includes_status(
    example_embedding_batch_result,
):
    changed = EmbeddingBatchResult(
        **{
            **dict(example_embedding_batch_result),
            "status": EmbeddingStatus.FAILED.value,
        }
    )
    assert hash(example_embedding_batch_result) != hash(changed)


# === DESERIALIZATION EDGE CASES ===


@pytest.mark.unit
def test_embedding_batch_result_missing_status_key():
    with pytest.raises(ValueError, match="missing keys"):
        item_to_embedding_batch_result(
            {
                "PK": {"S": "BATCH#abc"},
                "GSI3SK": {
                    "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#1#LINE#1#WORD#1#LABEL#ITEM"
                },
                "image_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "pinecone_id": {"S": "RECEIPT#1#LINE#1#WORD#1"},
                "text": {"S": "Bananas"},
                "error_message": {"NULL": True},
                "view": {"S": "WORD"},
            }
        )


@pytest.mark.unit
def test_item_to_embedding_batch_result_success(
    example_embedding_batch_result,
):
    item = example_embedding_batch_result.to_item()
    item["GSI3SK"] = {
        "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#1#LINE#2#WORD#3#LABEL#ITEM"
    }
    result = item_to_embedding_batch_result(item)
    assert isinstance(result, EmbeddingBatchResult)


@pytest.mark.unit
def test_embedding_batch_result_deserialization_raises():
    item = {
        "PK": {"S": "BATCH#abc"},
        "SK": {"S": "RESULT#RECEIPT#1#LINE#2#WORD#3#LABEL#ITEM"},
        "image_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "pinecone_id": {"S": "RECEIPT#1#LINE#2#WORD#3"},
        "text": {"S": "Bananas"},
        "status": {"S": "SUCCESS"},
        "error_message": {"NULL": True},
        "view": {"S": "WORD"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to EmbeddingBatchResult"
    ):
        item_to_embedding_batch_result(item)


@pytest.mark.unit
def test_embedding_batch_result_malformed_sk_parsing():
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_embedding_batch_result(
            {
                "batch_id": "bad",
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 101,
                "line_id": 2,
                "word_id": 3,
                "pinecone_id": "INVALID",
                "status": EmbeddingStatus.SUCCESS.value,
                "text": "Bananas",
                "error_message": {"S": "Malformed SK"},
                "view": {"S": "WORD"},
            }
        )


@pytest.mark.unit
def test_embedding_batch_result_deserialization_without_error_message():
    item = {
        "PK": {"S": "BATCH#dc7e61ba-5722-43a2-8e99-9df9f54287a9"},
        "SK": {
            "S": "RESULT#IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00101#LINE#00002#WORD#00003#LABEL#ITEM"
        },
        "image_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "pinecone_id": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00101#LINE#00002#WORD#00003"
        },
        "text": {"S": "Bananas"},
        "status": {"S": "SUCCESS"},
        "error_message": {"NULL": True},
        "view": {"S": "WORD"},
    }
    result = item_to_embedding_batch_result(item)
    assert result.error_message is None


@pytest.mark.unit
def test_embedding_batch_result_deserialization_with_error_message():
    item = {
        "PK": {"S": "BATCH#dc7e61ba-5722-43a2-8e99-9df9f54287a9"},
        "SK": {
            "S": "RESULT#IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00101#LINE#00002#WORD#00003#LABEL#ITEM"
        },
        "image_id": {"S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "pinecone_id": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00101#LINE#00002#WORD#00003"
        },
        "text": {"S": "Bananas"},
        "status": {"S": "SUCCESS"},
        "error_message": {"S": "Here is an error message"},
        "view": {"S": "WORD"},
    }
    result = item_to_embedding_batch_result(item)
    assert result.error_message == "Here is an error message"
