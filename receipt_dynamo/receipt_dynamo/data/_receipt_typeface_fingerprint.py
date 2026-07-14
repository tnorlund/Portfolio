"""DynamoDB operations for receipt typeface fingerprints."""

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_typeface_fingerprint import (
    ReceiptTypefaceFingerprint,
    item_to_receipt_typeface_fingerprint,
)


class _ReceiptTypefaceFingerprint(FlattenedStandardMixin):
    """Single-record CRUD for deterministic receipt fingerprints."""

    @handle_dynamodb_errors("put_receipt_typeface_fingerprint")
    def put_receipt_typeface_fingerprint(
        self, fingerprint: ReceiptTypefaceFingerprint
    ) -> None:
        """Create or replace the receipt-level additive artifact."""

        self._validate_entity(
            fingerprint, ReceiptTypefaceFingerprint, "fingerprint"
        )
        self._client.put_item(
            TableName=self.table_name,
            Item=fingerprint.to_item(),
        )

    @handle_dynamodb_errors("get_receipt_typeface_fingerprint")
    def get_receipt_typeface_fingerprint(
        self, image_id: str, receipt_id: int
    ) -> ReceiptTypefaceFingerprint:
        """Return the fingerprint for one receipt."""

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(f"RECEIPT#{receipt_id:05d}#TYPEFACE_FINGERPRINT"),
            entity_class=ReceiptTypefaceFingerprint,
            converter_func=item_to_receipt_typeface_fingerprint,
        )
        if result is None:
            raise EntityNotFoundError(
                f"No typeface fingerprint for {image_id}/{receipt_id}"
            )
        return result


__all__ = ["_ReceiptTypefaceFingerprint"]
