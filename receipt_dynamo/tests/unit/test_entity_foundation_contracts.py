"""Direct contracts for shared entity foundations and result containers."""

# These direct foundation contracts intentionally exercise protected helpers.
# pylint: disable=protected-access

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
    create_image_receipt_sk_parser,
    create_ocr_job_extractors,
    create_ocr_job_sk_parser,
    create_receipt_barcode_sk_parser,
    create_receipt_line_word_sk_parser,
)
from receipt_dynamo.entities.identifier_mixins import (
    ImageIdentifierMixin,
    ImageLineIdentifierMixin,
    ImageWordIdentifierMixin,
    JobIdentifierMixin,
    LineIdentifierMixin,
    ReceiptIdentifierMixin,
    WordIdentifierMixin,
)
from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_bundle import (
    ReceiptBundle,
    ReceiptBundlePage,
)
from receipt_dynamo.entities.receipt_details import ReceiptDetails
from receipt_dynamo.entities.receipt_text_geometry_entity import (
    ReceiptTextGeometryEntity,
)

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"
JOB_ID = "123e4567-e89b-42d3-a456-426614174000"
NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@dataclass
class ExampleEntity:
    """Small constructor target used to exercise EntityFactory directly."""

    image_id: str
    receipt_id: int
    name: str
    count: int = 0


@dataclass
class NestedEntity(DynamoDBEntity):
    """Concrete base entity with mutable nested state."""

    name: str
    metadata: dict[str, list[int]]


@dataclass
class ReceiptWordIdentifiers(WordIdentifierMixin):
    """Concrete receipt hierarchy identifier subject."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int


@dataclass
class ImageWordIdentifiers(ImageWordIdentifierMixin):
    """Concrete image hierarchy identifier subject."""

    image_id: str
    line_id: int
    word_id: int


@dataclass
class JobIdentifier(JobIdentifierMixin):
    """Concrete job identifier subject."""

    job_id: str


def make_receipt(**overrides: Any) -> Receipt:
    """Build a minimal valid receipt container value."""
    values: dict[str, Any] = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "width": 100,
        "height": 200,
        "timestamp_added": NOW,
        "raw_s3_bucket": "raw-bucket",
        "raw_s3_key": "receipt.png",
        "top_left": {"x": 0, "y": 0},
        "top_right": {"x": 1, "y": 0},
        "bottom_left": {"x": 0, "y": 1},
        "bottom_right": {"x": 1, "y": 1},
    }
    return Receipt(**(values | overrides))


def make_geometry(**overrides: Any) -> ReceiptTextGeometryEntity:
    """Build the receipt geometry base with valid stable fields."""
    values: dict[str, Any] = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "text": "subtotal",
        "bounding_box": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        "top_left": {"x": 0.0, "y": 0.0},
        "top_right": {"x": 1.0, "y": 0.0},
        "bottom_left": {"x": 0.0, "y": 1.0},
        "bottom_right": {"x": 1.0, "y": 1.0},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
        "confidence": 1.0,
    }
    return ReceiptTextGeometryEntity(**(values | overrides))


@pytest.mark.unit
def test_dynamodb_entity_iteration_and_to_dict_copy_nested_state() -> None:
    """Base iteration is ordered while to_dict owns its nested values."""
    entity = NestedEntity("record", {"ids": [1, 2]})

    assert list(entity) == [
        ("name", "record"),
        ("metadata", {"ids": [1, 2]}),
    ]
    result = entity.to_dict()
    result["metadata"]["ids"].append(3)

    assert entity.metadata == {"ids": [1, 2]}


@pytest.mark.unit
def test_dynamodb_entity_validate_keys_accepts_any_iterable() -> None:
    """Missing-key detection works for generators without mutating input."""
    item = {"PK": {"S": "one"}, "SK": {"S": "two"}}

    missing = DynamoDBEntity.validate_keys(
        item, (name for name in ("PK", "SK", "TYPE"))
    )

    assert missing == {"TYPE"}
    assert set(item) == {"PK", "SK"}


@pytest.mark.unit
def test_entity_factory_combines_key_parsers_extractors_and_mappings() -> None:
    """Factory routes key, custom, and mapped fields exactly once."""
    item = {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00007"},
        "display_name": {"S": "market"},
        "count": {"N": "3"},
    }

    entity = EntityFactory.create_entity(
        ExampleEntity,
        item,
        {"PK", "SK", "display_name", "count"},
        field_mappings={"display_name": "name"},
        custom_extractors={"count": EntityFactory.extract_int_field("count")},
        key_parsers={
            "PK": create_image_receipt_pk_parser(),
            "SK": create_image_receipt_sk_parser(),
        },
    )

    assert entity == ExampleEntity(IMAGE_ID, 7, "market", 3)


@pytest.mark.unit
def test_entity_factory_supports_historical_mapping_orientation() -> None:
    """Legacy constructor-to-item mappings remain readable."""

    @dataclass
    class NamedEntity:
        name: str

    entity = EntityFactory.create_entity(
        NamedEntity,
        {"display_name": {"S": "market"}},
        {"name"},
        field_mappings={"name": "display_name"},
    )

    assert entity == NamedEntity("market")


@pytest.mark.unit
def test_entity_factory_supports_constructor_name_in_required_keys() -> None:
    """Item-to-constructor mappings also resolve constructor keys."""

    @dataclass
    class NamedEntity:
        name: str

    entity = EntityFactory.create_entity(
        NamedEntity,
        {"display_name": {"S": "market"}},
        {"name"},
        field_mappings={"display_name": "name"},
    )

    assert entity == NamedEntity("market")


@pytest.mark.unit
def test_entity_factory_reports_missing_key_and_field_names() -> None:
    """Missing structural and constructor fields share the stable error."""

    @dataclass
    class NamedEntity:
        name: str

    with pytest.raises(
        ValueError, match="Item is missing required keys"
    ) as exc:
        EntityFactory.create_entity(NamedEntity, {}, {"PK", "name"})

    assert "PK" in str(exc.value)
    assert "name" in str(exc.value)


@pytest.mark.unit
def test_entity_factory_none_hooks_fall_back_without_being_called() -> None:
    """Optional hook slots are safe and required fields fall back."""

    @dataclass
    class NameEntity:
        name: str

    entity = EntityFactory.create_entity(
        NameEntity,
        {"name": {"S": "market"}},
        {"name"},
        custom_extractors={"name": None},
        key_parsers={"PK": None},
    )

    assert entity == NameEntity("market")


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_key",
    [42, {}, {"N": "1"}, {"S": ""}, {"S": "ok", "N": "1"}],
)
def test_entity_factory_rejects_malformed_dynamodb_keys(bad_key: Any) -> None:
    """Key parsers never receive malformed or ambiguous Dynamo attributes."""
    with pytest.raises(ValueError, match="Field 'PK' must"):
        EntityFactory.create_entity(
            ExampleEntity,
            {"PK": bad_key, "name": {"S": "market"}},
            {"PK", "name"},
            key_parsers={"PK": create_image_receipt_pk_parser()},
        )


@pytest.mark.unit
def test_entity_factory_wraps_constructor_validation() -> None:
    """Constructor failures retain causes behind the stable factory error."""

    @dataclass
    class RejectingEntity:
        name: str

        def __post_init__(self) -> None:
            raise ValueError("invalid name")

    with pytest.raises(
        ValueError, match="^Failed to create RejectingEntity: invalid name$"
    ) as raised:
        EntityFactory.create_entity(
            RejectingEntity, {"name": {"S": "x"}}, {"name"}
        )

    assert isinstance(raised.value.__cause__, ValueError)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pk", "sk", "match"),
    [
        ("RECEIPT#id", "RECEIPT#00001", "Invalid PK format"),
        ("IMAGE#", "RECEIPT#00001", "Invalid PK format"),
        (f"IMAGE#{IMAGE_ID}", "LINE#00001", "Invalid SK format"),
        (f"IMAGE#{IMAGE_ID}", "RECEIPT#", "Invalid SK format"),
        (f"IMAGE#{IMAGE_ID}", "RECEIPT#word", "invalid literal"),
    ],
)
def test_parse_image_receipt_key_rejects_malformed_components(
    pk: str, sk: str, match: str
) -> None:
    """Common key parsing rejects empty, mislabeled, and nonnumeric parts."""
    with pytest.raises(ValueError, match=match):
        EntityFactory.parse_image_receipt_key(pk, sk)


@pytest.mark.unit
def test_parse_image_receipt_key_preserves_suffix_for_specialized_parser() -> (
    None
):
    """The common parser returns suffix parts for hierarchical records."""
    parsed = EntityFactory.parse_image_receipt_key(
        f"IMAGE#{IMAGE_ID}", "RECEIPT#00002#BARCODE#00003"
    )

    assert parsed == {
        "image_id": IMAGE_ID,
        "receipt_id": 2,
        "sk_parts": ["RECEIPT", "00002", "BARCODE", "00003"],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "sk",
    [
        "RECEIPT#00001#ROW#00002#WORD#00003",
        "RECEIPT#00001#LINE#00002#TOKEN#00003",
        "RECEIPT#00001#LINE#00002#WORD#00003#EXTRA",
        "RECEIPT#00001#LINE#x#WORD#00003",
    ],
)
def test_receipt_line_word_parser_rejects_noncanonical_keys(sk: str) -> None:
    """Word keys require the exact receipt/line/word hierarchy."""
    with pytest.raises(ValueError, match="RECEIPT/LINE/WORD key"):
        create_receipt_line_word_sk_parser()(sk)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pk", "sk"),
    [
        ("RECEIPT#id", "RECEIPT#00001#LINE#00002#WORD#00003"),
        ("IMAGE#", "RECEIPT#00001#LINE#00002#WORD#00003"),
        (f"IMAGE#{IMAGE_ID}", "LINE#00002#WORD#00003"),
    ],
)
def test_parse_receipt_line_word_key_rejects_invalid_root_keys(
    pk: str, sk: str
) -> None:
    """The complete parser validates both the image and receipt roots."""
    with pytest.raises(ValueError, match="RECEIPT/LINE/WORD key"):
        EntityFactory.parse_receipt_line_word_key(pk, sk)


@pytest.mark.unit
def test_receipt_line_word_parser_returns_numeric_identifiers() -> None:
    """Canonical padded components deserialize as integers."""
    assert create_receipt_line_word_sk_parser()(
        "RECEIPT#00001#LINE#00002#WORD#00003"
    ) == {"receipt_id": 1, "line_id": 2, "word_id": 3}


@pytest.mark.unit
@pytest.mark.parametrize(
    "sk",
    [
        "RECEIPT#00001#WORD#00002",
        "RECEIPT#00001#BARCODE#",
        "RECEIPT#00001#BARCODE#00002#EXTRA",
        "RECEIPT#00001#BARCODE#x",
    ],
)
def test_receipt_barcode_parser_rejects_noncanonical_keys(sk: str) -> None:
    """Barcode keys require an exact labeled numeric component."""
    with pytest.raises(ValueError):
        create_receipt_barcode_sk_parser()(sk)


@pytest.mark.unit
def test_receipt_barcode_parser_returns_numeric_identifiers() -> None:
    """Canonical barcode keys deserialize all identifiers."""
    assert create_receipt_barcode_sk_parser()(
        "RECEIPT#00004#BARCODE#00005"
    ) == {"receipt_id": 4, "barcode_id": 5}


@pytest.mark.unit
@pytest.mark.parametrize(
    "sk", ["JOB#id", "OCR_JOB#", "OCR_JOB#id#EXTRA", "no-separator"]
)
def test_ocr_job_parser_rejects_noncanonical_keys(sk: str) -> None:
    """OCR keys accept only the two supported nonempty prefixes."""
    with pytest.raises(ValueError, match="Invalid OCR job SK format"):
        create_ocr_job_sk_parser()(sk)


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ["OCR_JOB", "ROUTING"])
def test_ocr_job_parser_accepts_both_record_prefixes(prefix: str) -> None:
    """Job and routing records share the same identifier parser."""
    assert create_ocr_job_sk_parser()(f"{prefix}#job-1") == {"job_id": "job-1"}


@pytest.mark.unit
def test_entity_factory_scalar_extractors_handle_defaults_and_zero() -> None:
    """Scalar extractors distinguish absent fields from explicit zero."""
    item = {
        "empty": {"S": ""},
        "zero": {"N": "0"},
        "fraction": {"N": "1.25"},
        "timestamp": {"S": NOW.isoformat()},
    }

    assert EntityFactory.extract_string_field("missing", "fallback")(item) == (
        "fallback"
    )
    assert EntityFactory.extract_string_field("empty", "fallback")(item) == (
        "fallback"
    )
    assert EntityFactory.extract_int_field("zero", 9)(item) == 0
    assert EntityFactory.extract_int_field("missing", 9)(item) == 9
    assert EntityFactory.extract_float_field("fraction", 9.0)(item) == 1.25
    assert EntityFactory.extract_float_field("missing", 9.0)(item) == 9.0
    assert EntityFactory.extract_datetime_field("timestamp")(item) == NOW
    assert (
        EntityFactory.extract_string_field("null", "fallback")(
            {"null": {"NULL": True}}
        )
        == "fallback"
    )
    assert (
        EntityFactory.extract_int_field("null", 9)({"null": {"NULL": True}})
        == 9
    )
    assert (
        EntityFactory.extract_float_field("null", 9.0)(
            {"null": {"NULL": True}}
        )
        == 9.0
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extractor", "item", "match"),
    [
        (
            EntityFactory.extract_string_field("value"),
            {"value": {"S": 123}},
            "DynamoDB string",
        ),
        (
            EntityFactory.extract_string_field("value"),
            {"value": {"S": "ok", "N": "1"}},
            "DynamoDB string",
        ),
        (
            EntityFactory.extract_int_field("value"),
            {"value": {"N": True}},
            "DynamoDB number",
        ),
        (
            EntityFactory.extract_int_field("value"),
            {"value": {"S": "1"}},
            "DynamoDB number",
        ),
        (
            EntityFactory.extract_float_field("value"),
            {"value": {"N": "nan"}},
            "must be finite",
        ),
        (
            EntityFactory.extract_float_field("value"),
            {"value": {"S": "1.5"}},
            "DynamoDB number",
        ),
        (
            EntityFactory.extract_datetime_field("value"),
            {"value": {"S": 123}},
            "DynamoDB string",
        ),
        (
            EntityFactory.extract_datetime_field("value"),
            {},
            "Missing required field",
        ),
    ],
)
def test_entity_factory_scalar_extractors_reject_malformed_attributes(
    extractor: Any, item: dict[str, Any], match: str
) -> None:
    """Explicit malformed attributes never silently become defaults."""
    with pytest.raises(ValueError, match=match):
        extractor(item)


@pytest.mark.unit
def test_entity_factory_string_list_extractor_owns_result() -> None:
    """Entity mutation cannot change the source DynamoDB item."""
    item = {"labels": {"SS": ["TOTAL", "TAX"]}}

    labels = EntityFactory.extract_string_list_field("labels")(item)
    labels.append("DATE")

    assert item == {"labels": {"SS": ["TOTAL", "TAX"]}}
    assert not EntityFactory.extract_string_list_field("missing")(item)
    assert not EntityFactory.extract_string_list_field("labels")(
        {"labels": {"NULL": True}}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "attribute",
    [
        {"SS": "TOTAL"},
        {"SS": []},
        {"SS": ["TOTAL", 1]},
        {"SS": ["TOTAL", ""]},
        {"SS": ["TOTAL", "TOTAL"]},
        {"SS": ["TOTAL"], "S": "TOTAL"},
        {"L": [{"S": "TOTAL"}]},
    ],
)
def test_string_list_extractor_rejects_malformed_sets(
    attribute: dict[str, Any],
) -> None:
    """String sets require an exact SS shape and unique string payloads."""
    with pytest.raises(ValueError, match="labels must"):
        EntityFactory.extract_string_list_field("labels")(
            {"labels": attribute}
        )


@pytest.mark.unit
def test_entity_factory_optional_and_receipt_extractors() -> None:
    """Optional fields distinguish missing, partial, and populated maps."""
    assert EntityFactory.extract_optional_extracted_data({}) is None
    assert (
        EntityFactory.extract_optional_extracted_data(
            {"extracted_data": {"NULL": True}}
        )
        is None
    )
    assert EntityFactory.extract_optional_extracted_data(
        {"extracted_data": {"M": {"type": {"S": "currency"}}}}
    ) == {"type": "currency"}
    assert EntityFactory.extract_optional_extracted_data(
        {
            "extracted_data": {
                "M": {
                    "type": {"S": "currency"},
                    "value": {"S": "$1.00"},
                }
            }
        }
    ) == {"type": "currency", "value": "$1.00"}
    assert (
        EntityFactory.extract_optional_extracted_data(
            {"extracted_data": {"M": {}}}
        )
        is None
    )
    assert EntityFactory.extract_embedding_status({}) == "NONE"
    assert (
        EntityFactory.extract_embedding_status(
            {"embedding_status": {"S": "SUCCESS"}}
        )
        == "SUCCESS"
    )
    assert (
        EntityFactory.extract_embedding_status(
            {"embedding_status": {"NULL": True}}
        )
        == "NONE"
    )
    assert EntityFactory.extract_is_noise({}) is False
    assert (
        EntityFactory.extract_is_noise({"is_noise": {"NULL": True}}) is False
    )
    assert EntityFactory.extract_is_noise({"is_noise": {"BOOL": True}})


@pytest.mark.unit
@pytest.mark.parametrize(
    "attribute",
    [
        {"M": "value"},
        {"M": {"type": {"S": 1}}},
        {"M": {"type": {"S": "currency", "N": "1"}}},
        {"M": {"other": {"S": "value"}}},
        {"M": {}, "NULL": False},
        None,
    ],
)
def test_extracted_data_rejects_malformed_attributes(attribute: Any) -> None:
    """Optional extracted data validates its complete nested Dynamo shape."""
    with pytest.raises(ValueError, match="extracted_data"):
        EntityFactory.extract_optional_extracted_data(
            {"extracted_data": attribute}
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "attribute",
    [{"N": "1"}, {"S": 1}, {"S": "NONE", "N": "1"}, None],
)
def test_embedding_status_rejects_malformed_attributes(attribute: Any) -> None:
    """An explicit malformed status cannot silently become NONE."""
    with pytest.raises(ValueError, match="DynamoDB string"):
        EntityFactory.extract_embedding_status({"embedding_status": attribute})


@pytest.mark.unit
@pytest.mark.parametrize(
    "attribute",
    [{"S": "true"}, {"BOOL": 1}, {"BOOL": True, "S": "true"}, None],
)
def test_is_noise_rejects_malformed_attributes(attribute: Any) -> None:
    """An explicit malformed noise flag cannot silently become false."""
    with pytest.raises(ValueError, match="DynamoDB boolean"):
        EntityFactory.extract_is_noise({"is_noise": attribute})


@pytest.mark.unit
def test_entity_factory_text_extractor_requires_dynamodb_string() -> None:
    """Text extraction reports missing and wrong DynamoDB attribute types."""
    with pytest.raises(ValueError, match="Missing required field: text"):
        EntityFactory.extract_text_field({})
    with pytest.raises(ValueError, match="must be a string type"):
        EntityFactory.extract_text_field({"text": {"N": "1"}})
    with pytest.raises(ValueError, match="must be a string type"):
        EntityFactory.extract_text_field({"text": {"S": 1}})
    with pytest.raises(ValueError, match="must be a string type"):
        EntityFactory.extract_text_field({"text": {"S": "one", "N": "1"}})
    assert EntityFactory.extract_text_field({"text": {"S": "one"}}) == "one"


@pytest.mark.unit
def test_geometry_extractors_decode_each_field_independently() -> None:
    """The geometry extractor map decodes all shared DynamoDB shapes."""

    def point(x: int, y: int) -> dict[str, Any]:
        return {"M": {"x": {"N": str(x)}, "y": {"N": str(y)}}}

    item = {
        "bounding_box": {
            "M": {
                "x": {"N": "0"},
                "y": {"N": "1"},
                "width": {"N": "2"},
                "height": {"N": "3"},
            }
        },
        "top_left": point(0, 1),
        "top_right": point(2, 1),
        "bottom_left": point(0, 4),
        "bottom_right": point(2, 4),
        "angle_degrees": {"N": "90"},
        "angle_radians": {"N": "1.5"},
        "confidence": {"N": "0.75"},
    }

    values = {
        name: extractor(item)
        for name, extractor in create_geometry_extractors().items()
    }

    assert values["bounding_box"] == {
        "x": 0.0,
        "y": 1.0,
        "width": 2.0,
        "height": 3.0,
    }
    assert values["bottom_right"] == {"x": 2.0, "y": 4.0}
    assert values["angle_degrees"] == 90.0
    assert values["angle_radians"] == 1.5
    assert values["confidence"] == 0.75


@pytest.mark.unit
def test_ocr_job_extractors_parse_defaults_and_datetimes() -> None:
    """Shared OCR extractors retain missing optional update timestamps."""
    item: dict[str, Any] = {
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "created_at": {"S": NOW.isoformat()},
        "status": {"S": "PENDING"},
    }
    extractors = create_ocr_job_extractors()

    assert {
        name: extractor(item) for name, extractor in extractors.items()
    } == {
        "s3_bucket": "bucket",
        "s3_key": "key",
        "created_at": NOW,
        "updated_at": None,
        "status": "PENDING",
    }
    item["updated_at"] = {"S": NOW.isoformat()}
    assert extractors["updated_at"](item) == NOW
    item["updated_at"] = {"NULL": True}
    assert extractors["updated_at"](item) is None


@pytest.mark.unit
def test_identifier_mixins_validate_and_format_receipt_hierarchy() -> None:
    """Receipt identifier helpers validate once and generate padded keys."""
    identifiers = ReceiptWordIdentifiers(IMAGE_ID, 2, 3, 4)

    identifiers._validate_word_identifiers()

    assert identifiers._pk_image() == {"PK": {"S": f"IMAGE#{IMAGE_ID}"}}
    assert identifiers._sk_receipt() == "RECEIPT#00002"
    assert identifiers._composite_image_receipt() == (
        f"IMAGE#{IMAGE_ID}#RECEIPT#00002"
    )
    assert identifiers._sk_receipt_line() == "RECEIPT#00002#LINE#00003"
    assert identifiers._sk_receipt_line_word() == (
        "RECEIPT#00002#LINE#00003#WORD#00004"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mixin", "attribute", "value", "validator", "match"),
    [
        (
            ImageIdentifierMixin,
            "image_id",
            "bad",
            "_validate_image_id",
            "UUIDv4",
        ),
        (
            ReceiptIdentifierMixin,
            "receipt_id",
            True,
            "_validate_receipt_id",
            "integer",
        ),
        (
            LineIdentifierMixin,
            "line_id",
            False,
            "_validate_line_id",
            "integer",
        ),
        (
            WordIdentifierMixin,
            "word_id",
            -1,
            "_validate_word_id",
            "non-negative",
        ),
        (
            ImageLineIdentifierMixin,
            "line_id",
            0,
            "_validate_line_id",
            "positive",
        ),
        (
            ImageWordIdentifierMixin,
            "word_id",
            True,
            "_validate_word_id",
            "integer",
        ),
        (JobIdentifierMixin, "job_id", "bad", "_validate_job_id", "UUIDv4"),
    ],
)
def test_identifier_mixins_reject_invalid_boundaries(
    mixin: type,
    attribute: str,
    value: Any,
    validator: str,
    match: str,
) -> None:
    """All hierarchy levels reject bool-as-int and invalid identifiers."""
    subject = mixin()
    setattr(subject, attribute, value)

    with pytest.raises(ValueError, match=match):
        getattr(subject, validator)()


@pytest.mark.unit
def test_image_and_job_identifier_helpers_generate_canonical_keys() -> None:
    """Nonreceipt and job hierarchies use their documented key formats."""
    image_ids = ImageWordIdentifiers(IMAGE_ID, 1, 0)
    job_id = JobIdentifier(JOB_ID)

    image_ids._validate_image_word_identifiers()
    job_id._validate_job_id()

    assert image_ids._sk_line() == "LINE#00001"
    assert image_ids._sk_line_word() == "LINE#00001#WORD#00000"
    assert job_id._pk_job() == {"PK": {"S": f"JOB#{JOB_ID}"}}


@pytest.mark.unit
def test_receipt_bundle_page_mapping_and_alias_contracts() -> None:
    """The page behaves as a live mapping and preserves its legacy alias."""
    bundle = ReceiptBundle(make_receipt(), [], [])
    page = ReceiptBundlePage(
        {bundle.key: bundle},
        {"PK": {"S": f"IMAGE#{IMAGE_ID}"}, "SK": {"S": "RECEIPT#00001"}},
    )

    assert bundle.key == f"{IMAGE_ID}_1"
    assert page.summaries is page.bundles
    assert len(page) == 1
    assert bundle.key in page
    assert list(page) == [bundle.key]
    assert list(page.keys()) == [bundle.key]
    assert list(page.values()) == [bundle]
    assert list(page.items()) == [(bundle.key, bundle)]
    assert page[bundle.key] is bundle
    assert page.has_more
    with pytest.raises(KeyError):
        _ = page["missing"]


@pytest.mark.unit
def test_receipt_bundle_page_none_token_marks_final_page() -> None:
    """A final empty page reports no pagination continuation."""
    page = ReceiptBundlePage({}, None)

    assert not page.has_more
    assert not page


@pytest.mark.unit
def test_receipt_details_defaults_and_iteration_are_stable() -> None:
    """Container defaults do not leak and iteration keeps stable ordering."""
    receipt = make_receipt()
    first = ReceiptDetails(receipt, [], [], [])
    second = ReceiptDetails(receipt, [], [], [])

    first.letters.append("letter")  # type: ignore[arg-type]
    first.barcodes.append("barcode")  # type: ignore[arg-type]

    assert not second.letters
    assert not second.barcodes
    assert second.resolved_details is None
    assert list(first) == [
        receipt,
        [],
        [],
        ["letter"],
        [],
        None,
        ["barcode"],
    ]


@pytest.mark.unit
def test_receipt_geometry_normalizes_and_serializes_status() -> None:
    """Receipt geometry normalizes enum input into stable DynamoDB values."""
    entity = make_geometry(
        embedding_status=EmbeddingStatus.SUCCESS, is_noise=True
    )

    assert entity.embedding_status == "SUCCESS"
    assert entity._get_receipt_fields_for_serialization() == {
        "embedding_status": {"S": "SUCCESS"},
        "is_noise": {"BOOL": True},
    }
    assert entity.REQUIRED_KEYS is entity.BASE_REQUIRED_KEYS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"receipt_id": True}, "must be an integer"),
        ({"receipt_id": 0}, "must be positive"),
        ({"embedding_status": "unknown"}, "must be one of"),
        ({"embedding_status": 1}, "string or EmbeddingStatus"),
        ({"is_noise": 1}, "must be a boolean"),
    ],
)
def test_receipt_geometry_rejects_invalid_receipt_fields(
    overrides: dict[str, Any], match: str
) -> None:
    """Base receipt fields enforce bool-safe IDs, enums, and booleans."""
    with pytest.raises(ValueError, match=match):
        make_geometry(**overrides)
