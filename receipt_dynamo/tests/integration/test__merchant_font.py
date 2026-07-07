"""
Integration tests for MerchantFont operations in DynamoDB.
"""

from datetime import datetime, timezone
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.merchant_font import MerchantFont

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def regular_merchant_font() -> MerchantFont:
    return MerchantFont(
        merchant_name="Sprouts Farmers Market",
        face="regular",
        s3_bucket="merchant-fonts",
        s3_key="sprouts/regular.npz",
        content_hash="a" * 64,
        source_commit="abc1234",
        compiled_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        cap_h=0.72,
        advance_ratio=0.544,
        pitch_check="0.544 vs 0.545 OK",
        glyph_count=96,
        stylemap_s3_key="sprouts/stylemap.json",
    )


@pytest.fixture
def heavy_merchant_font() -> MerchantFont:
    return MerchantFont(
        merchant_name="Sprouts Farmers Market",
        face="heavy",
        s3_bucket="merchant-fonts",
        s3_key="sprouts/heavy.npz",
        content_hash="b" * 64,
        source_commit="abc1234",
        compiled_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        cap_h=0.72,
        advance_ratio=0.544,
        pitch_check="0.544 vs 0.545 OK",
        glyph_count=80,
    )


# -------------------------------------------------------------------
#                    SUCCESSFUL OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestMerchantFontOperations:
    """Test merchant font CRUD operations."""

    def test_add_and_get_regular(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        regular_merchant_font: MerchantFont,
    ) -> None:
        client = DynamoClient(dynamodb_table)
        client.add_merchant_font(regular_merchant_font)

        # Default face is "regular"
        result = client.get_merchant_font("Sprouts Farmers Market")
        assert result == regular_merchant_font

    def test_get_explicit_face(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        heavy_merchant_font: MerchantFont,
    ) -> None:
        client = DynamoClient(dynamodb_table)
        client.add_merchant_font(heavy_merchant_font)

        result = client.get_merchant_font(
            "Sprouts Farmers Market", face="heavy"
        )
        assert result == heavy_merchant_font

    def test_get_not_found(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        client = DynamoClient(dynamodb_table)
        assert client.get_merchant_font("Nonexistent Merchant") is None

    def test_add_is_overwrite(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        regular_merchant_font: MerchantFont,
    ) -> None:
        client = DynamoClient(dynamodb_table)
        client.add_merchant_font(regular_merchant_font)

        updated = MerchantFont(
            merchant_name="Sprouts Farmers Market",
            face="regular",
            s3_bucket="merchant-fonts",
            s3_key="sprouts/regular-v2.npz",
            content_hash="c" * 64,
            source_commit="deadbeef",
            compiled_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            cap_h=0.73,
            advance_ratio=0.545,
            pitch_check="0.545 vs 0.545 OK",
            glyph_count=100,
        )
        client.add_merchant_font(updated)

        result = client.get_merchant_font("Sprouts Farmers Market")
        assert result == updated
        assert result.s3_key == "sprouts/regular-v2.npz"

    def test_list_merchant_fonts(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        regular_merchant_font: MerchantFont,
        heavy_merchant_font: MerchantFont,
    ) -> None:
        client = DynamoClient(dynamodb_table)
        client.add_merchant_font(regular_merchant_font)
        client.add_merchant_font(heavy_merchant_font)

        fonts = client.list_merchant_fonts("Sprouts Farmers Market")
        assert len(fonts) == 2
        faces = {f.face for f in fonts}
        assert faces == {"regular", "heavy"}

    def test_list_merchant_fonts_empty(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        client = DynamoClient(dynamodb_table)
        assert client.list_merchant_fonts("Nonexistent Merchant") == []

    def test_delete_merchant_font(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        regular_merchant_font: MerchantFont,
    ) -> None:
        client = DynamoClient(dynamodb_table)
        client.add_merchant_font(regular_merchant_font)
        assert client.get_merchant_font("Sprouts Farmers Market") is not None

        client.delete_merchant_font("Sprouts Farmers Market", "regular")
        assert client.get_merchant_font("Sprouts Farmers Market") is None

    def test_delete_is_idempotent(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        client = DynamoClient(dynamodb_table)
        # Deleting a missing item does not raise.
        client.delete_merchant_font("Nonexistent Merchant", "regular")


# -------------------------------------------------------------------
#                    VALIDATION
# -------------------------------------------------------------------


@pytest.mark.integration
class TestMerchantFontValidation:
    def test_add_none_raises(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError):
            client.add_merchant_font(None)  # type: ignore

    def test_add_wrong_type_raises(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError):
            client.add_merchant_font("not-a-font")  # type: ignore

    def test_get_empty_merchant_raises(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError):
            client.get_merchant_font("")
