from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.base_operations.shared_utils import (
    validate_pagination_params,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import item_to_receipt_barcode
from receipt_dynamo.entities.receipt_barcode import ReceiptBarcode


class _ReceiptBarcode(FlattenedStandardMixin):
    """Data-access methods for ReceiptBarcode entities.

    Methods
    -------
    add_receipt_barcode(receipt_barcode)
        Adds a single ReceiptBarcode.
    add_receipt_barcodes(receipt_barcodes)
        Adds multiple ReceiptBarcodes.
    delete_receipt_barcode(receipt_barcode)
        Deletes a single ReceiptBarcode.
    delete_receipt_barcodes(receipt_barcodes)
        Deletes multiple ReceiptBarcodes.
    delete_receipt_barcodes_from_receipt(image_id, receipt_id)
        Deletes all ReceiptBarcodes under a receipt (idempotent backfill helper).
    get_receipt_barcode(image_id, receipt_id, barcode_id)
        Retrieves a single ReceiptBarcode.
    list_receipt_barcodes(limit, last_evaluated_key)
        Returns all ReceiptBarcodes from the table (paginated).
    list_receipt_barcodes_from_receipt(image_id, receipt_id)
        Returns all ReceiptBarcodes under a receipt.
    """

    @handle_dynamodb_errors("add_receipt_barcode")
    def add_receipt_barcode(self, receipt_barcode: ReceiptBarcode) -> None:
        """Adds a single ReceiptBarcode to DynamoDB."""
        if receipt_barcode is None:
            raise EntityValidationError("receipt_barcode cannot be None")
        if not isinstance(receipt_barcode, ReceiptBarcode):
            raise EntityValidationError(
                "receipt_barcode must be an instance of ReceiptBarcode"
            )
        self._add_entity(
            receipt_barcode, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_receipt_barcodes")
    def add_receipt_barcodes(
        self, receipt_barcodes: list[ReceiptBarcode]
    ) -> None:
        """Adds multiple ReceiptBarcodes to DynamoDB."""
        if receipt_barcodes is None:
            raise EntityValidationError("receipt_barcodes cannot be None")
        if not isinstance(receipt_barcodes, list):
            raise EntityValidationError("receipt_barcodes must be a list")
        for i, receipt_barcode in enumerate(receipt_barcodes):
            if not isinstance(receipt_barcode, ReceiptBarcode):
                raise EntityValidationError(
                    f"receipt_barcodes[{i}] must be an instance of "
                    f"ReceiptBarcode, got {type(receipt_barcode).__name__}"
                )
        self._add_entities(
            receipt_barcodes, ReceiptBarcode, "receipt_barcodes"
        )

    @handle_dynamodb_errors("delete_receipt_barcode")
    def delete_receipt_barcode(
        self, receipt_barcode: ReceiptBarcode
    ) -> None:
        """Deletes a single ReceiptBarcode."""
        if receipt_barcode is None:
            raise EntityValidationError("receipt_barcode cannot be None")
        if not isinstance(receipt_barcode, ReceiptBarcode):
            raise EntityValidationError(
                "receipt_barcode must be an instance of ReceiptBarcode"
            )
        self._delete_entity(
            receipt_barcode, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_receipt_barcodes")
    def delete_receipt_barcodes(
        self, receipt_barcodes: list[ReceiptBarcode]
    ) -> None:
        """Deletes multiple ReceiptBarcodes in batch."""
        if receipt_barcodes is None:
            raise EntityValidationError("receipt_barcodes cannot be None")
        if not isinstance(receipt_barcodes, list):
            raise EntityValidationError("receipt_barcodes must be a list")
        for i, receipt_barcode in enumerate(receipt_barcodes):
            if not isinstance(receipt_barcode, ReceiptBarcode):
                raise EntityValidationError(
                    f"receipt_barcodes[{i}] must be an instance of "
                    f"ReceiptBarcode, got {type(receipt_barcode).__name__}"
                )
        self._delete_entities(receipt_barcodes)

    @handle_dynamodb_errors("delete_receipt_barcodes_from_receipt")
    def delete_receipt_barcodes_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> None:
        """Deletes all ReceiptBarcodes under a receipt.

        Lets a backfill re-run idempotently (delete then re-add).
        """
        self.delete_receipt_barcodes(
            self.list_receipt_barcodes_from_receipt(image_id, receipt_id)
        )

    @handle_dynamodb_errors("get_receipt_barcode")
    def get_receipt_barcode(
        self, image_id: str, receipt_id: int, barcode_id: int
    ) -> ReceiptBarcode:
        """Retrieves a single ReceiptBarcode by IDs."""
        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        if not isinstance(barcode_id, int) or barcode_id < 0:
            raise EntityValidationError(
                "barcode_id must be a non-negative integer"
            )
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#BARCODE#{barcode_id:05d}",
            entity_class=ReceiptBarcode,
            converter_func=item_to_receipt_barcode,
        )
        if result is None:
            raise EntityNotFoundError(
                f"ReceiptBarcode with image_id={image_id}, "
                f"receipt_id={receipt_id}, barcode_id={barcode_id} not found"
            )
        return result

    @handle_dynamodb_errors("list_receipt_barcodes")
    def list_receipt_barcodes(
        self,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptBarcode], dict[str, Any] | None]:
        """Returns all ReceiptBarcodes from the table (paginated)."""
        validate_pagination_params(limit, last_evaluated_key)
        return self._query_by_type(
            entity_type="RECEIPT_BARCODE",
            converter_func=item_to_receipt_barcode,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_barcodes_from_receipt")
    def list_receipt_barcodes_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptBarcode]:
        """Returns all ReceiptBarcodes under a specific receipt/image."""
        return self._query_entities_by_receipt_gsi3(
            image_id, receipt_id, "BARCODE", item_to_receipt_barcode
        )
