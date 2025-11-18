"""Data access for ReceiptCoreLabelDefinition entities."""

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
from receipt_dynamo.entities.receipt_core_label_definition import (
    ReceiptCoreLabelDefinition,
    item_to_receipt_core_label_definition,
)

if TYPE_CHECKING:
    pass


class _ReceiptCoreLabelDefinition(FlattenedStandardMixin):
    """Accessor methods for ReceiptCoreLabelDefinition items in DynamoDB."""

    @handle_dynamodb_errors("add_receipt_core_label_definition")
    def add_receipt_core_label_definition(
        self, definition: ReceiptCoreLabelDefinition
    ) -> None:
        """Add a CORE_LABEL definition to DynamoDB."""
        if definition is None:
            raise EntityValidationError("definition cannot be None")
        if not isinstance(definition, ReceiptCoreLabelDefinition):
            raise EntityValidationError(
                "definition must be an instance of ReceiptCoreLabelDefinition"
            )
        self._add_entity(definition)

    @handle_dynamodb_errors("add_receipt_core_label_definitions")
    def add_receipt_core_label_definitions(
        self, definitions: List[ReceiptCoreLabelDefinition]
    ) -> None:
        """Add multiple CORE_LABEL definitions to DynamoDB."""
        if definitions is None:
            raise EntityValidationError("definitions cannot be None")
        if not isinstance(definitions, list) or not all(
            isinstance(d, ReceiptCoreLabelDefinition) for d in definitions
        ):
            raise EntityValidationError(
                "definitions must be a list of ReceiptCoreLabelDefinition objects"
            )
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=d.to_item())
            )
            for d in definitions
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_core_label_definition")
    def get_receipt_core_label_definition(
        self, label_type: str
    ) -> Optional[ReceiptCoreLabelDefinition]:
        """Get a CORE_LABEL definition by label type."""
        return self._get_entity(
            primary_key="CORE_LABEL_DEFINITION",
            sort_key=f"LABEL#{label_type.upper()}",
            entity_class=ReceiptCoreLabelDefinition,
            converter_func=item_to_receipt_core_label_definition,
        )

    @handle_dynamodb_errors("list_receipt_core_label_definitions")
    def list_receipt_core_label_definitions(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptCoreLabelDefinition], Optional[Dict[str, Any]]]:
        """List all CORE_LABEL definitions."""
        return self._query_by_type(
            entity_type="RECEIPT_CORE_LABEL_DEFINITION",
            converter_func=item_to_receipt_core_label_definition,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("query_core_label_definitions_by_category")
    def query_core_label_definitions_by_category(
        self,
        category: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptCoreLabelDefinition], Optional[Dict[str, Any]]]:
        """Query CORE_LABEL definitions by category using GSI1."""
        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :gsi1pk AND begins_with(GSI1SK, :gsi1sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":gsi1pk": {"S": f"CATEGORY#{category}"},
                ":gsi1sk": {"S": "LABEL#"},
            },
            converter_func=item_to_receipt_core_label_definition,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

