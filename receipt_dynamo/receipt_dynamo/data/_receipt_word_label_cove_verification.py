"""Data access for ReceiptWordLabelCoVeVerification entities."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_word_label_cove_verification import (
    ReceiptWordLabelCoVeVerification,
    item_to_receipt_word_label_cove_verification,
)

if TYPE_CHECKING:
    pass


class _ReceiptWordLabelCoVeVerification(FlattenedStandardMixin):
    """Accessor methods for ReceiptWordLabelCoVeVerification items in DynamoDB."""

    @handle_dynamodb_errors("add_receipt_word_label_cove_verification")
    def add_receipt_word_label_cove_verification(
        self, verification: ReceiptWordLabelCoVeVerification
    ) -> None:
        """Add a CoVe verification record to DynamoDB."""
        if verification is None:
            raise EntityValidationError("verification cannot be None")
        if not isinstance(verification, ReceiptWordLabelCoVeVerification):
            raise EntityValidationError(
                "verification must be an instance of ReceiptWordLabelCoVeVerification"
            )
        self._add_entity(verification)

    @handle_dynamodb_errors("add_receipt_word_label_cove_verifications")
    def add_receipt_word_label_cove_verifications(
        self, verifications: List[ReceiptWordLabelCoVeVerification]
    ) -> None:
        """Add multiple CoVe verification records to DynamoDB."""
        if verifications is None:
            raise EntityValidationError("verifications cannot be None")
        if not isinstance(verifications, list) or not all(
            isinstance(v, ReceiptWordLabelCoVeVerification) for v in verifications
        ):
            raise EntityValidationError(
                "verifications must be a list of ReceiptWordLabelCoVeVerification objects"
            )
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=v.to_item())
            )
            for v in verifications
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_word_label_cove_verification")
    def get_receipt_word_label_cove_verification(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        label: str,
    ) -> Optional[ReceiptWordLabelCoVeVerification]:
        """Get a CoVe verification record by its key."""
        return self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"
                f"#WORD#{word_id:05d}#LABEL#{label.upper()}#COVE"
            ),
            entity_class=ReceiptWordLabelCoVeVerification,
            converter_func=item_to_receipt_word_label_cove_verification,
        )

    @handle_dynamodb_errors("query_cove_verifications_by_label")
    def query_cove_verifications_by_label(
        self,
        label: str,
        cove_verified: Optional[bool] = None,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabelCoVeVerification], Optional[Dict[str, Any]]]:
        """Query CoVe verifications by label type using GSI1."""
        label_upper = label.upper()

        # Build GSI1PK
        gsi1pk = f"LABEL#{label_upper}#COVE"

        # Build GSI1SK prefix based on cove_verified filter
        if cove_verified is not None:
            gsi1sk_prefix = f"VERIFIED#{cove_verified}#"
        else:
            gsi1sk_prefix = "VERIFIED#"

        # Build key condition expression
        key_condition = "GSI1PK = :gsi1pk"
        expression_attribute_values = {":gsi1pk": {"S": gsi1pk}}

        # Add SK prefix filter if needed
        if cove_verified is not None:
            key_condition += " AND begins_with(GSI1SK, :gsi1sk)"
            expression_attribute_values[":gsi1sk"] = {"S": gsi1sk_prefix}

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression=key_condition,
            expression_attribute_names=None,
            expression_attribute_values=expression_attribute_values,
            converter_func=item_to_receipt_word_label_cove_verification,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("query_cove_verifications_by_merchant")
    def query_cove_verifications_by_merchant(
        self,
        merchant_name: str,
        label: Optional[str] = None,
        can_be_reused_as_template: Optional[bool] = None,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptWordLabelCoVeVerification], Optional[Dict[str, Any]]]:
        """Query CoVe verifications by merchant using GSI2 (for template lookup)."""
        merchant_normalized = merchant_name.strip().upper()

        if label:
            label_upper = label.upper()
            gsi2pk = f"MERCHANT#{merchant_normalized}#LABEL#{label_upper}"
        else:
            gsi2pk = f"MERCHANT#{merchant_normalized}#LABEL#"

        # Build GSI2SK prefix based on template filter
        if can_be_reused_as_template is not None:
            gsi2sk_prefix = f"TEMPLATE#{can_be_reused_as_template}#"
        else:
            gsi2sk_prefix = "TEMPLATE#"

        # Build key condition expression
        key_condition = "GSI2PK = :gsi2pk"
        expression_attribute_values = {":gsi2pk": {"S": gsi2pk}}

        # Add SK prefix filter if needed
        if can_be_reused_as_template is not None:
            key_condition += " AND begins_with(GSI2SK, :gsi2sk)"
            expression_attribute_values[":gsi2sk"] = {"S": gsi2sk_prefix}

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression=key_condition,
            expression_attribute_names=None,
            expression_attribute_values=expression_attribute_values,
            converter_func=item_to_receipt_word_label_cove_verification,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

