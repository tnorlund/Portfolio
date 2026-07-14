"""Focused boundary contracts for miscellaneous Dynamo record entities."""

from datetime import datetime
from time import time

import pytest

from receipt_dynamo.constants import (
    BatchStatus,
    EmbeddingStatus,
    LabelStatus,
    OCRJobType,
    OCRStatus,
)
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
)
from receipt_dynamo.entities.embedding_batch_result import (
    EmbeddingBatchResult,
)
from receipt_dynamo.entities.image_details import ImageDetails
from receipt_dynamo.entities.label_count_cache import LabelCountCache
from receipt_dynamo.entities.label_hygiene_result import LabelHygieneResult
from receipt_dynamo.entities.label_metadata import LabelMetadata
from receipt_dynamo.entities.merchant_font import MerchantFont
from receipt_dynamo.entities.ocr_job import OCRJob
from receipt_dynamo.entities.ocr_routing_decision import OCRRoutingDecision
from receipt_dynamo.entities.places_cache import PlacesCache

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
JOB_ID = "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
BATCH_ID = "42bffa3b-1a9e-4d2c-bb6a-08f0b2b5c123"
NOW = datetime(2024, 1, 1, 12)


def make_completion(**overrides):
    values = {
        "batch_id": BATCH_ID,
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 0,
        "word_id": 0,
        "original_label": "TOTAL",
        "gpt_suggested_label": None,
        "status": BatchStatus.PENDING,
        "validated_at": NOW,
    }
    values.update(overrides)
    return CompletionBatchResult(**values)


def make_embedding(**overrides):
    values = {
        "batch_id": BATCH_ID,
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 2,
        "word_id": 3,
        "pinecone_id": (
            f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002#WORD#00003"
        ),
        "status": EmbeddingStatus.SUCCESS,
        "text": "text",
        "error_message": None,
    }
    values.update(overrides)
    return EmbeddingBatchResult(**values)


def make_label_cache(**overrides):
    values = {
        "label": "TOTAL",
        "valid_count": 1,
        "invalid_count": 2,
        "pending_count": 3,
        "needs_review_count": 4,
        "none_count": 5,
        "last_updated": NOW.isoformat(),
    }
    values.update(overrides)
    return LabelCountCache(**values)


def make_hygiene(**overrides):
    values = {
        "hygiene_id": BATCH_ID,
        "alias": "SUB_TOTAL",
        "canonical_label": "SUBTOTAL",
        "reasoning": "equivalent",
        "gpt_agreed": True,
        "source_batch_id": None,
        "example_ids": [],
        "timestamp": NOW,
        "image_id": IMAGE_ID,
        "receipt_id": 1,
    }
    values.update(overrides)
    return LabelHygieneResult(**values)


def make_metadata(**overrides):
    values = {
        "label": "TOTAL",
        "status": LabelStatus.ACTIVE,
        "aliases": [],
        "description": "Total",
        "schema_version": 1,
        "last_updated": NOW,
    }
    values.update(overrides)
    return LabelMetadata(**values)


def make_font(**overrides):
    values = {
        "merchant_name": "Merchant",
        "face": "regular",
        "s3_bucket": "bucket",
        "s3_key": "font.npz",
        "content_hash": "a" * 64,
        "source_commit": "abc123",
        "compiled_at": NOW,
        "cap_h": 0.7,
        "advance_ratio": 0.5,
        "pitch_check": "ok",
        "glyph_count": 10,
    }
    values.update(overrides)
    return MerchantFont(**values)


def make_ocr_job(**overrides):
    values = {
        "image_id": IMAGE_ID,
        "job_id": JOB_ID,
        "s3_bucket": "bucket",
        "s3_key": "image.png",
        "created_at": NOW,
        "updated_at": None,
        "status": OCRStatus.PENDING,
        "job_type": OCRJobType.REGIONAL_REOCR,
        "receipt_id": 1,
        "reocr_region": {"x": 0, "y": 1, "width": 2, "height": 3},
        "reocr_reason": "retry",
    }
    values.update(overrides)
    return OCRJob(**values)


def make_routing(**overrides):
    values = {
        "image_id": IMAGE_ID,
        "job_id": JOB_ID,
        "s3_bucket": "bucket",
        "s3_key": "image.png",
        "created_at": NOW,
        "updated_at": None,
        "receipt_count": 1,
        "status": OCRStatus.PENDING,
    }
    values.update(overrides)
    return OCRRoutingDecision(**values)


def make_places(**overrides):
    values = {
        "search_type": "ADDRESS",
        "search_value": "123 Main St",
        "place_id": "place-1",
        "places_response": {"nested": {"values": [1, 2]}},
        "last_updated": NOW.isoformat(),
        "query_count": 0,
    }
    values.update(overrides)
    return PlacesCache(**values)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (make_completion, "receipt_id"),
        (make_completion, "line_id"),
        (make_completion, "word_id"),
        (make_embedding, "receipt_id"),
        (make_embedding, "line_id"),
        (make_embedding, "word_id"),
        (make_label_cache, "valid_count"),
        (make_label_cache, "invalid_count"),
        (make_label_cache, "pending_count"),
        (make_label_cache, "needs_review_count"),
        (make_label_cache, "none_count"),
        (make_label_cache, "time_to_live"),
        (make_hygiene, "receipt_id"),
        (make_metadata, "schema_version"),
        (make_routing, "receipt_count"),
        (make_places, "query_count"),
        (make_places, "time_to_live"),
    ],
)
def test_integer_fields_reject_bool(factory, field):
    with pytest.raises(ValueError):
        factory(**{field: True})


def test_completion_preserves_empty_optional_label_and_exact_gsis():
    result = make_completion(gpt_suggested_label="")
    item = result.to_item()

    assert item["gpt_suggested_label"] == {"S": ""}
    assert item["GSI2SK"] == {"S": "STATUS#PENDING"}
    assert item["GSI3PK"] == {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00001"}
    assert CompletionBatchResult.from_item(item) == result


def test_embedding_rejects_pinecone_id_for_a_different_image():
    wrong_id = "5f52804b-2fad-4e00-92c8-b593da3a8ed5"
    with pytest.raises(ValueError, match="pinecone_id must be in the format"):
        make_embedding(
            pinecone_id=(
                f"IMAGE#{wrong_id}#RECEIPT#00001#LINE#00002#WORD#00003"
            )
        )


def test_embedding_preserves_empty_error_and_uses_accessor_padding():
    result = make_embedding(error_message="")
    item = result.to_item()

    assert item["error_message"] == {"S": ""}
    assert item["SK"]["S"].endswith("#RECEIPT#00001#LINE#002#WORD#003")
    assert EmbeddingBatchResult.from_item(item) == result


def test_label_cache_preserves_expired_ttl_and_revalidates_mutation():
    expired = int(time()) - 60
    cache = make_label_cache(time_to_live=expired)

    assert LabelCountCache.from_item(cache.to_item()).time_to_live == expired

    cache.valid_count = False
    with pytest.raises(ValueError, match="valid_count"):
        cache.to_item()


def test_label_cache_iter_repr_and_legacy_ttl_contract():
    cache = make_label_cache(time_to_live=123)
    item = cache.to_item()
    item["TimeToLive"] = item.pop("time_to_live")

    restored = LabelCountCache.from_item(item)

    assert dict(restored)["time_to_live"] == 123
    assert "time_to_live=123" in repr(restored)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("PK", {"S": "WRONG"}, "PK"),
        ("TYPE", {"S": "WRONG"}, "TYPE"),
        ("SK", {"S": "WRONG"}, "LabelCountCache"),
        ("valid_count", {"N": "not-an-int"}, "LabelCountCache"),
    ],
)
def test_label_cache_rejects_malformed_items(field, value, message):
    item = make_label_cache().to_item()
    item[field] = value

    with pytest.raises(ValueError, match=message):
        LabelCountCache.from_item(item)


def test_hygiene_empty_collections_and_none_roundtrip_losslessly():
    result = make_hygiene()
    item = result.to_item()

    assert item["source_batch_id"] == {"NULL": True}
    assert item["example_ids"] == {"L": []}
    assert item["receipt_id"] == {"N": "1"}
    assert LabelHygieneResult.from_item(item) == result


def test_hygiene_detaches_example_ids_and_revalidates_mutation():
    examples = ["example"]
    result = make_hygiene(example_ids=examples)
    examples.append("caller mutation")
    assert result.example_ids == ["example"]

    result.receipt_id = True
    with pytest.raises(ValueError, match="receipt_id"):
        result.to_item()


def test_string_set_fields_reject_duplicates_before_dynamo_loses_them():
    with pytest.raises(ValueError, match="duplicates"):
        make_hygiene(example_ids=["same", "same"])
    with pytest.raises(ValueError, match="duplicates"):
        make_metadata(aliases=["same", "same"])


def test_label_metadata_defaults_are_independent_and_dynamo_safe():
    first = make_metadata()
    second = make_metadata(label="SUBTOTAL")
    first.aliases.append("GRAND_TOTAL")
    first.receipt_refs.append((IMAGE_ID, 1))

    assert second.aliases == []
    assert second.receipt_refs == []
    item = second.to_item()
    assert item["aliases"] == {"L": []}
    assert item["receipt_refs"] == {"L": []}
    assert LabelMetadata.from_item(item) == second


def test_label_metadata_emits_conditional_gsi2_and_copies_inputs():
    aliases = ["SUM"]
    refs = [(IMAGE_ID, 1)]
    metadata = make_metadata(
        aliases=aliases, receipt_refs=refs, label_target="value"
    )
    aliases.append("caller")
    refs.append((IMAGE_ID, 2))

    assert metadata.aliases == ["SUM"]
    assert metadata.receipt_refs == [(IMAGE_ID, 1)]
    assert metadata.to_item()["GSI2PK"] == {"S": "LABEL_TARGET#value"}


@pytest.mark.parametrize("field", ["cap_h", "advance_ratio"])
@pytest.mark.parametrize(
    "bad_value", [True, 0, -1, float("nan"), float("inf")]
)
def test_merchant_font_rejects_non_positive_or_non_finite_numbers(
    field, bad_value
):
    with pytest.raises(ValueError):
        make_font(**{field: bad_value})


def test_merchant_font_revalidates_mutation_and_type_marker():
    font = make_font()
    item = font.to_item()
    assert MerchantFont.from_item(item) == font

    item["TYPE"] = {"S": "WRONG"}
    with pytest.raises(ValueError, match="TYPE"):
        MerchantFont.from_item(item)

    font.cap_h = float("nan")
    with pytest.raises(ValueError, match="cap_h"):
        font.to_item()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("receipt_id", True),
        ("receipt_id", 0),
        ("reocr_region", {"x": True, "y": 0, "width": 1, "height": 1}),
        (
            "reocr_region",
            {"x": 0, "y": 0, "width": float("nan"), "height": 1},
        ),
        ("reocr_region", {"x": 0, "y": 0, "width": 0, "height": 1}),
    ],
)
def test_ocr_job_rejects_invalid_numeric_boundaries(field, bad_value):
    with pytest.raises(ValueError):
        make_ocr_job(**{field: bad_value})


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("s3_bucket", 1),
        ("s3_bucket", ""),
        ("s3_key", 1),
        ("s3_key", ""),
        ("created_at", "2024-01-01"),
        ("updated_at", "2024-01-01"),
        ("receipt_id", "1"),
        ("reocr_region", []),
        ("reocr_region", {"x": 0, "y": 0, "width": 1}),
        ("reocr_reason", 1),
        ("status", "UNKNOWN"),
        ("job_type", "UNKNOWN"),
    ],
)
def test_ocr_job_rejects_invalid_scalar_and_shape_fields(field, bad_value):
    with pytest.raises(ValueError):
        make_ocr_job(**{field: bad_value})


def test_ocr_job_nested_map_roundtrip_copy_and_exact_keys():
    region = {"x": 0, "y": 1, "width": 2, "height": 3}
    job = make_ocr_job(reocr_region=region)
    region["width"] = 99
    item = job.to_item()

    assert job.reocr_region["width"] == 2
    assert item["GSI1PK"] == {"S": "OCR_JOB_STATUS#PENDING"}
    assert item["GSI2PK"] == {"S": "OCR_JOB_STATUS#PENDING"}
    assert item["reocr_region"]["M"]["width"] == {"N": "2.0"}
    assert OCRJob.from_item(item) == job

    assert dict(job)["reocr_reason"] == "retry"
    assert "OCRJob(" in repr(job)
    assert hash(OCRJob.from_item(item)) == hash(job)
    assert job != "not-an-ocr-job"


def test_ocr_job_optional_fields_roundtrip_as_null():
    job = make_ocr_job(
        receipt_id=None,
        reocr_region=None,
        reocr_reason=None,
        updated_at=None,
    )
    item = job.to_item()

    assert item["receipt_id"] == {"NULL": True}
    assert item["reocr_region"] == {"NULL": True}
    assert item["reocr_reason"] == {"NULL": True}
    assert OCRJob.from_item(item) == job


def test_ocr_job_from_item_tolerates_stale_gsi_status_partition():
    """Legacy rows keep the GSI status partition they were written with.

    Real OCRJob rows in dev/prod were persisted at status=PENDING and never had
    their GSI status partitions rewritten when the status advanced, so a row can
    carry status=COMPLETED while GSI1PK/GSI2PK still read OCR_JOB_STATUS#PENDING.
    from_item must read these rows (the status field is authoritative), not
    hard-fail them with "Invalid OCRJob keys".
    """
    job = make_ocr_job(status=OCRStatus.COMPLETED)
    item = job.to_item()
    # Reproduce the legacy shape: stale-but-valid PENDING partitions.
    item["GSI1PK"] = {"S": "OCR_JOB_STATUS#PENDING"}
    item["GSI2PK"] = {"S": "OCR_JOB_STATUS#PENDING"}

    restored = OCRJob.from_item(item)

    assert restored == job
    assert restored.status == OCRStatus.COMPLETED.value


@pytest.mark.parametrize("key", ["GSI1PK", "GSI2PK"])
def test_ocr_job_from_item_rejects_malformed_gsi_status_partition(key):
    """A GSI status partition that is not a valid OCR_JOB_STATUS#<status> is
    still rejected, so genuine corruption is not silently accepted."""
    item = make_ocr_job().to_item()
    item[key] = {"S": "OCR_JOB_STATUS#NOT_A_STATUS"}

    with pytest.raises(ValueError, match="keys"):
        OCRJob.from_item(item)


def test_ocr_job_revalidates_nested_map_after_mutation():
    job = make_ocr_job()
    job.reocr_region["height"] = float("inf")

    with pytest.raises(ValueError, match="reocr_region"):
        job.to_item()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"TYPE": {"S": "WRONG"}}, "TYPE"),
        ({"SK": {"S": "ROUTING#wrong"}}, "SK"),
        ({"GSI1PK": {"S": "WRONG"}}, "keys"),
        ({"reocr_region": {"S": "wrong"}}, "map"),
        (
            {
                "reocr_region": {
                    "M": {
                        "x": {"N": "0"},
                        "y": {"N": "0"},
                        "width": {"N": "1"},
                    }
                }
            },
            "missing numeric field",
        ),
        ({"reocr_reason": {"N": "1"}}, "DynamoDB string"),
    ],
)
def test_ocr_job_rejects_inconsistent_or_malformed_items(mutation, message):
    item = make_ocr_job().to_item()
    item.update(mutation)

    with pytest.raises(ValueError, match=message):
        OCRJob.from_item(item)


def test_routing_rejects_negative_counts_and_revalidates_mutation():
    with pytest.raises(ValueError, match="non-negative"):
        make_routing(receipt_count=-1)

    routing = make_routing()
    assert OCRRoutingDecision.from_item(routing.to_item()) == routing
    routing.receipt_count = True
    with pytest.raises(ValueError, match="receipt_count"):
        routing.to_item()


def test_places_response_is_copied_and_nested_values_roundtrip():
    response = {"nested": {"values": [1, 2]}}
    cache = make_places(places_response=response)
    response["nested"]["values"].append(3)

    restored = PlacesCache.from_item(cache.to_item())

    assert restored.places_response == {"nested": {"values": [1, 2]}}
    assert restored == cache


@pytest.mark.parametrize(
    "bad_response",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
    ],
)
def test_places_rejects_non_json_response_values(bad_response):
    with pytest.raises(ValueError, match="JSON serializable"):
        make_places(places_response=bad_response)


@pytest.mark.parametrize(
    ("search_type", "search_value"),
    [
        ("ADDRESS", "x" * 401),
        ("PHONE", "1" * 31),
        ("URL", "x" * 101),
        ("PHONE", "letters only"),
    ],
)
def test_places_rejects_key_values_outside_supported_bounds(
    search_type, search_value
):
    cache = make_places(search_type=search_type, search_value=search_value)
    with pytest.raises(ValueError):
        cache.to_item()


def test_places_ttl_zero_is_visible_and_mutations_are_revalidated():
    cache = make_places(time_to_live=0)
    assert dict(cache)["time_to_live"] == 0
    assert "time_to_live=0" in repr(cache)

    cache.query_count = True
    with pytest.raises(ValueError, match="query_count"):
        cache.to_item()


def test_places_search_type_mutation_clears_address_only_fields():
    cache = make_places()
    assert cache.normalized_value and cache.value_hash
    cache.search_type = "URL"
    cache.search_value = "example.com"

    item = cache.to_item()

    assert "normalized_value" not in item
    assert "value_hash" not in item


def test_image_details_defaults_and_input_containers_are_independent():
    source = [object()]
    details = ImageDetails(images=source)
    other = ImageDetails()
    source.append(object())

    assert len(details.images) == 1
    assert not other.images
    details.lines.append(object())
    assert not other.lines
    assert len(list(details)) == 13


def test_image_details_rejects_non_list_collections():
    with pytest.raises(ValueError, match="images must be a list"):
        ImageDetails(images=())
