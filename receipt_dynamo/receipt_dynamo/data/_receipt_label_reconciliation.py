"""DynamoDB operations for deterministic label reconciliation artifacts."""

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_label_reconciliation import (
    ReceiptLabelReconciliation,
    item_to_receipt_label_reconciliation,
)


class _ReceiptLabelReconciliation(FlattenedStandardMixin):
    """Single-record CRUD for a receipt's D3 reconciliation artifact."""

    @handle_dynamodb_errors("put_receipt_label_reconciliation")
    def put_receipt_label_reconciliation(
        self, reconciliation: ReceiptLabelReconciliation
    ) -> None:
        """Create or replace one receipt reconciliation artifact."""

        self._validate_entity(
            reconciliation, ReceiptLabelReconciliation, "reconciliation"
        )
        self._client.put_item(
            TableName=self.table_name,
            Item=reconciliation.to_item(),
        )

    @handle_dynamodb_errors("get_receipt_label_reconciliation")
    def get_receipt_label_reconciliation(
        self, image_id: str, receipt_id: int
    ) -> ReceiptLabelReconciliation:
        """Return one receipt reconciliation artifact."""

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#LABEL_RECONCILIATION",
            entity_class=ReceiptLabelReconciliation,
            converter_func=item_to_receipt_label_reconciliation,
        )
        if result is None:
            raise EntityNotFoundError(
                f"No label reconciliation for {image_id}/{receipt_id}"
            )
        return result


__all__ = ["_ReceiptLabelReconciliation"]
