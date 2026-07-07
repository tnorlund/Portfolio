"""
Accessor methods for MerchantFont items in DynamoDB.

MerchantFont items are "current pointers" to compiled merchant font artifacts
in S3. Writes overwrite any existing pointer for the same (merchant, face);
this entity does not keep history.
"""

from typing import TYPE_CHECKING

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.merchant_font import (
    MerchantFont,
    item_to_merchant_font,
)

if TYPE_CHECKING:
    pass


class _MerchantFont(FlattenedStandardMixin):
    """Accessor methods for MerchantFont items in DynamoDB."""

    @handle_dynamodb_errors("add_merchant_font")
    def add_merchant_font(self, font: MerchantFont) -> None:
        """
        Adds (or overwrites) a merchant font pointer.

        This is a "current pointer" write: any existing item for the same
        (merchant, face) is replaced.

        Args:
            font: The MerchantFont to store.

        Raises:
            EntityValidationError: If font is None or wrong type.
        """
        if font is None:
            raise EntityValidationError("font cannot be None")
        if not isinstance(font, MerchantFont):
            raise EntityValidationError(
                "font must be an instance of MerchantFont"
            )
        # No condition expression: overwrite is the intended behavior.
        self._add_entity(font)

    @handle_dynamodb_errors("get_merchant_font")
    def get_merchant_font(
        self, merchant_name: str, face: str = "regular"
    ) -> MerchantFont | None:
        """
        Retrieves a merchant font pointer by merchant and face.

        Args:
            merchant_name: The exact merchant string.
            face: The font face ("regular" or "heavy").

        Returns:
            The MerchantFont if found, None otherwise.
        """
        if not merchant_name:
            raise EntityValidationError("merchant_name cannot be empty")
        if not face:
            raise EntityValidationError("face cannot be empty")

        return self._get_entity(
            primary_key=f"MERCHANT_FONT#{merchant_name}",
            sort_key=f"FACE#{face}",
            entity_class=MerchantFont,
            converter_func=item_to_merchant_font,
        )

    @handle_dynamodb_errors("list_merchant_fonts")
    def list_merchant_fonts(
        self, merchant_name: str
    ) -> list[MerchantFont]:
        """
        Lists all font faces for a merchant.

        Args:
            merchant_name: The exact merchant string.

        Returns:
            List of MerchantFonts for the merchant (one per face).
        """
        if not merchant_name:
            raise EntityValidationError("merchant_name cannot be empty")

        fonts, _ = self._query_by_parent(
            parent_key_prefix=f"MERCHANT_FONT#{merchant_name}",
            child_key_prefix="FACE#",
            converter_func=item_to_merchant_font,
        )
        return fonts

    @handle_dynamodb_errors("delete_merchant_font")
    def delete_merchant_font(self, merchant_name: str, face: str) -> None:
        """
        Deletes a merchant font pointer.

        Args:
            merchant_name: The exact merchant string.
            face: The font face ("regular" or "heavy").

        Raises:
            EntityValidationError: If merchant_name or face is empty.
        """
        if not merchant_name:
            raise EntityValidationError("merchant_name cannot be empty")
        if not face:
            raise EntityValidationError("face cannot be empty")

        self._client.delete_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"MERCHANT_FONT#{merchant_name}"},
                "SK": {"S": f"FACE#{face}"},
            },
        )
