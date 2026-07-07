# pylint: disable=redefined-outer-name,unused-variable
"""Unit tests for MerchantFont entity."""

from datetime import datetime

import pytest

from receipt_dynamo.entities.merchant_font import (
    MerchantFont,
    item_to_merchant_font,
)


@pytest.fixture
def example_merchant_font():
    return MerchantFont(
        merchant_name="Sprouts Farmers Market",
        face="regular",
        s3_bucket="merchant-fonts",
        s3_key="sprouts/regular.npz",
        content_hash="a" * 64,
        source_commit="abc1234",
        compiled_at=datetime(2024, 1, 1, 12, 0, 0),
        cap_h=0.72,
        advance_ratio=0.544,
        pitch_check="0.544 vs 0.545 OK",
        glyph_count=96,
        stylemap_s3_key="sprouts/stylemap.json",
    )


@pytest.fixture
def minimal_merchant_font():
    return MerchantFont(
        merchant_name="Costco Wholesale",
        face="heavy",
        s3_bucket="merchant-fonts",
        s3_key="costco/heavy.npz",
        content_hash="b" * 64,
        source_commit="def5678",
        compiled_at="2024-01-01T12:00:00",
        cap_h=0.7,
        advance_ratio=0.6,
        pitch_check="0.600 vs 0.601 OK",
        glyph_count=64,
    )


# === VALID CONSTRUCTION ===


@pytest.mark.unit
def test_merchant_font_init_valid_datetime(example_merchant_font):
    assert example_merchant_font.merchant_name == "Sprouts Farmers Market"
    assert example_merchant_font.face == "regular"
    assert isinstance(example_merchant_font.compiled_at, str)
    assert example_merchant_font.cap_h == 0.72
    assert example_merchant_font.glyph_count == 96


@pytest.mark.unit
def test_merchant_font_init_valid_string_date(minimal_merchant_font):
    assert minimal_merchant_font.merchant_name == "Costco Wholesale"
    assert minimal_merchant_font.face == "heavy"
    assert minimal_merchant_font.compiled_at == "2024-01-01T12:00:00"
    assert minimal_merchant_font.stylemap_s3_key is None


@pytest.mark.unit
def test_merchant_font_int_numbers_coerced_to_float():
    font = MerchantFont(
        merchant_name="Test Merchant",
        face="regular",
        s3_bucket="b",
        s3_key="k",
        content_hash="h",
        source_commit="c",
        compiled_at="2024-01-01T12:00:00",
        cap_h=1,
        advance_ratio=1,
        pitch_check="ok",
        glyph_count=10,
    )
    assert isinstance(font.cap_h, float)
    assert isinstance(font.advance_ratio, float)


@pytest.mark.unit
def test_merchant_font_to_item_and_back(example_merchant_font):
    item = example_merchant_font.to_item()
    reconstructed = item_to_merchant_font(item)
    assert reconstructed == example_merchant_font


@pytest.mark.unit
def test_merchant_font_to_item_and_back_minimal(minimal_merchant_font):
    item = minimal_merchant_font.to_item()
    reconstructed = item_to_merchant_font(item)
    assert reconstructed == minimal_merchant_font


@pytest.mark.unit
def test_merchant_font_key_format(example_merchant_font):
    key = example_merchant_font.key
    assert key["PK"]["S"] == "MERCHANT_FONT#Sprouts Farmers Market"
    assert key["SK"]["S"] == "FACE#regular"


@pytest.mark.unit
def test_merchant_font_to_item_structure(example_merchant_font):
    item = example_merchant_font.to_item()
    for field in (
        "PK",
        "SK",
        "TYPE",
        "s3_bucket",
        "s3_key",
        "content_hash",
        "source_commit",
        "compiled_at",
        "cap_h",
        "advance_ratio",
        "pitch_check",
        "glyph_count",
        "stylemap_s3_key",
    ):
        assert field in item
    assert item["TYPE"]["S"] == "MERCHANT_FONT"
    assert item["cap_h"]["N"] == "0.72"
    assert item["glyph_count"]["N"] == "96"
    assert item["stylemap_s3_key"]["S"] == "sprouts/stylemap.json"


@pytest.mark.unit
def test_merchant_font_to_item_null_stylemap(minimal_merchant_font):
    item = minimal_merchant_font.to_item()
    assert item["stylemap_s3_key"]["NULL"] is True


# === INVALID CONSTRUCTION ===


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None, ""])
def test_merchant_font_invalid_merchant_name(bad_value):
    with pytest.raises((ValueError, TypeError)):
        MerchantFont(
            merchant_name=bad_value,
            face="regular",
            s3_bucket="b",
            s3_key="k",
            content_hash="h",
            source_commit="c",
            compiled_at="2024-01-01T12:00:00",
            cap_h=0.7,
            advance_ratio=0.5,
            pitch_check="ok",
            glyph_count=10,
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", ["bold", "", "Regular", 123])
def test_merchant_font_invalid_face(bad_value):
    with pytest.raises((ValueError, TypeError)):
        MerchantFont(
            merchant_name="Test Merchant",
            face=bad_value,
            s3_bucket="b",
            s3_key="k",
            content_hash="h",
            source_commit="c",
            compiled_at="2024-01-01T12:00:00",
            cap_h=0.7,
            advance_ratio=0.5,
            pitch_check="ok",
            glyph_count=10,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "field_name",
    ["s3_bucket", "s3_key", "content_hash", "source_commit", "pitch_check"],
)
def test_merchant_font_invalid_empty_string_fields(field_name):
    kwargs = dict(
        merchant_name="Test Merchant",
        face="regular",
        s3_bucket="b",
        s3_key="k",
        content_hash="h",
        source_commit="c",
        compiled_at="2024-01-01T12:00:00",
        cap_h=0.7,
        advance_ratio=0.5,
        pitch_check="ok",
        glyph_count=10,
    )
    kwargs[field_name] = ""
    with pytest.raises(ValueError, match=f"{field_name} must be"):
        MerchantFont(**kwargs)


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [None, 123, "not-a-date"])
def test_merchant_font_invalid_compiled_at_type(bad_value):
    # str "not-a-date" is accepted (validated at usage), None/int rejected.
    if isinstance(bad_value, str):
        font = MerchantFont(
            merchant_name="Test Merchant",
            face="regular",
            s3_bucket="b",
            s3_key="k",
            content_hash="h",
            source_commit="c",
            compiled_at=bad_value,
            cap_h=0.7,
            advance_ratio=0.5,
            pitch_check="ok",
            glyph_count=10,
        )
        assert font.compiled_at == bad_value
    else:
        with pytest.raises(ValueError, match="compiled_at must be"):
            MerchantFont(
                merchant_name="Test Merchant",
                face="regular",
                s3_bucket="b",
                s3_key="k",
                content_hash="h",
                source_commit="c",
                compiled_at=bad_value,
                cap_h=0.7,
                advance_ratio=0.5,
                pitch_check="ok",
                glyph_count=10,
            )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", ["x", None, True])
def test_merchant_font_invalid_glyph_count(bad_value):
    with pytest.raises((ValueError, TypeError)):
        MerchantFont(
            merchant_name="Test Merchant",
            face="regular",
            s3_bucket="b",
            s3_key="k",
            content_hash="h",
            source_commit="c",
            compiled_at="2024-01-01T12:00:00",
            cap_h=0.7,
            advance_ratio=0.5,
            pitch_check="ok",
            glyph_count=bad_value,
        )


@pytest.mark.unit
def test_merchant_font_negative_glyph_count():
    with pytest.raises(ValueError, match="glyph_count must be non-negative"):
        MerchantFont(
            merchant_name="Test Merchant",
            face="regular",
            s3_bucket="b",
            s3_key="k",
            content_hash="h",
            source_commit="c",
            compiled_at="2024-01-01T12:00:00",
            cap_h=0.7,
            advance_ratio=0.5,
            pitch_check="ok",
            glyph_count=-1,
        )


# === DATETIME HANDLING ===


@pytest.mark.unit
def test_merchant_font_datetime_to_iso_conversion():
    dt = datetime(2024, 1, 1, 12, 0, 0)
    font = MerchantFont(
        merchant_name="Test Merchant",
        face="regular",
        s3_bucket="b",
        s3_key="k",
        content_hash="h",
        source_commit="c",
        compiled_at=dt,
        cap_h=0.7,
        advance_ratio=0.5,
        pitch_check="ok",
        glyph_count=10,
    )
    assert font.compiled_at == "2024-01-01T12:00:00"


# === PARSING FAILURE ===


@pytest.mark.unit
def test_merchant_font_missing_keys():
    with pytest.raises(ValueError, match="missing keys"):
        item_to_merchant_font({})


@pytest.mark.unit
def test_merchant_font_invalid_pk_format():
    item = {
        "PK": {"S": "INVALID_FORMAT"},
        "SK": {"S": "FACE#regular"},
        "TYPE": {"S": "MERCHANT_FONT"},
        "s3_bucket": {"S": "b"},
        "s3_key": {"S": "k"},
        "content_hash": {"S": "h"},
        "source_commit": {"S": "c"},
        "compiled_at": {"S": "2024-01-01T12:00:00"},
        "cap_h": {"N": "0.7"},
        "advance_ratio": {"N": "0.5"},
        "pitch_check": {"S": "ok"},
        "glyph_count": {"N": "10"},
    }
    with pytest.raises(ValueError, match="Invalid MerchantFont PK format"):
        item_to_merchant_font(item)


# === MERCHANT NAME WITH SPECIAL CHARACTERS ===


@pytest.mark.unit
def test_merchant_font_merchant_name_with_hash():
    font = MerchantFont(
        merchant_name="A#B Market",
        face="regular",
        s3_bucket="b",
        s3_key="k",
        content_hash="h",
        source_commit="c",
        compiled_at="2024-01-01T12:00:00",
        cap_h=0.7,
        advance_ratio=0.5,
        pitch_check="ok",
        glyph_count=10,
    )
    reconstructed = item_to_merchant_font(font.to_item())
    assert reconstructed.merchant_name == "A#B Market"


# === EQUALITY, HASHING, STR ===


@pytest.mark.unit
def test_merchant_font_eq_and_hash(example_merchant_font):
    duplicate = item_to_merchant_font(example_merchant_font.to_item())
    assert duplicate == example_merchant_font
    assert hash(duplicate) == hash(example_merchant_font)
    assert example_merchant_font != "not-a-font"


@pytest.mark.unit
def test_merchant_font_repr(example_merchant_font):
    repr_str = repr(example_merchant_font)
    assert "MerchantFont(" in repr_str
    assert example_merchant_font.merchant_name in repr_str
    assert "regular" in repr_str
