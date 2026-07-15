"""DynamoDB operations for structured resolved receipt details."""

from typing import cast

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_resolved_details import (
    ReceiptResolvedDetails,
    item_to_receipt_resolved_details,
)


class _ReceiptResolvedDetails(FlattenedStandardMixin):
    """Single-record CRUD for a receipt's D4 resolved output."""

    @handle_dynamodb_errors("put_receipt_resolved_details")
    def put_receipt_resolved_details(
        self, resolved_details: ReceiptResolvedDetails
    ) -> None:
        """Create or replace one structured receipt-details artifact."""

        self._validate_entity(
            resolved_details, ReceiptResolvedDetails, "resolved_details"
        )
        self._client.put_item(
            TableName=self.table_name,
            Item=resolved_details.to_item(),
        )

    @handle_dynamodb_errors("get_receipt_resolved_details")
    def get_receipt_resolved_details(
        self, image_id: str, receipt_id: int
    ) -> ReceiptResolvedDetails:
        """Return one structured receipt-details artifact."""

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#RESOLVED_DETAILS",
            entity_class=ReceiptResolvedDetails,
            converter_func=item_to_receipt_resolved_details,
        )
        if result is None:
            raise EntityNotFoundError(
                f"No resolved details for {image_id}/{receipt_id}"
            )
        return cast(ReceiptResolvedDetails, result)


__all__ = ["_ReceiptResolvedDetails"]
