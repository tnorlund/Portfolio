from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.ocr_routing_decision import (
    OCRRoutingDecision,
    item_to_ocr_routing_decision,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


class _OCRRoutingDecision(FlattenedStandardMixin):
    @handle_dynamodb_errors("add_ocr_routing_decision")
    def add_ocr_routing_decision(self, ocr_routing_decision: OCRRoutingDecision):
        if ocr_routing_decision is None:
            raise EntityValidationError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise EntityValidationError(
                "ocr_routing_decision must be an instance of " "OCRRoutingDecision"
            )
        self._add_entity(ocr_routing_decision)

    @handle_dynamodb_errors("add_ocr_routing_decisions")
    def add_ocr_routing_decisions(
        self, ocr_routing_decisions: list[OCRRoutingDecision]
    ):
        if ocr_routing_decisions is None:
            raise EntityValidationError("ocr_routing_decisions cannot be None")
        if not isinstance(ocr_routing_decisions, list):
            raise EntityValidationError("ocr_routing_decisions must be a list")
        if not all(
            isinstance(decision, OCRRoutingDecision)
            for decision in ocr_routing_decisions
        ):
            raise EntityValidationError(
                "All items in ocr_routing_decisions must be instances of "
                "OCRRoutingDecision"
            )

        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=decision.to_item()))
            for decision in ocr_routing_decisions
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_ocr_routing_decision")
    def update_ocr_routing_decision(self, ocr_routing_decision: OCRRoutingDecision):
        if ocr_routing_decision is None:
            raise EntityValidationError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise EntityValidationError(
                "ocr_routing_decision must be an instance of " "OCRRoutingDecision"
            )

        self._update_entity(ocr_routing_decision)

    @handle_dynamodb_errors("get_ocr_routing_decision")
    def get_ocr_routing_decision(
        self, image_id: str, job_id: str
    ) -> OCRRoutingDecision:
        self._validate_image_id(image_id)
        assert_valid_uuid(job_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"ROUTING#{job_id}",
            entity_class=OCRRoutingDecision,
            converter_func=item_to_ocr_routing_decision,
        )

        if result is None:
            raise EntityNotFoundError(
                f"OCR routing decision for Image ID '{image_id}' "
                f"and Job ID '{job_id}' not found"
            )

        return result

    @handle_dynamodb_errors("delete_ocr_routing_decision")
    def delete_ocr_routing_decision(self, ocr_routing_decision: OCRRoutingDecision):
        if ocr_routing_decision is None:
            raise EntityValidationError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise EntityValidationError(
                "ocr_routing_decision must be an instance of " "OCRRoutingDecision"
            )
        self._delete_entity(ocr_routing_decision)

    def delete_ocr_routing_decisions(
        self, ocr_routing_decisions: list[OCRRoutingDecision]
    ):
        if ocr_routing_decisions is None:
            raise EntityValidationError("ocr_routing_decisions cannot be None")
        if not isinstance(ocr_routing_decisions, list):
            raise EntityValidationError("ocr_routing_decisions must be a list")
        if not all(
            isinstance(decision, OCRRoutingDecision)
            for decision in ocr_routing_decisions
        ):
            raise EntityValidationError(
                "All ocr_routing_decisions must be instances of " "OCRRoutingDecision"
            )
        transact_items = []
        for item in ocr_routing_decisions:
            transact_items.append(
                TransactWriteItemTypeDef(
                    Delete=DeleteTypeDef(
                        TableName=self.table_name,
                        Key=item.key,
                        ConditionExpression=(
                            "attribute_exists(PK) AND attribute_exists(SK)"
                        ),
                    )
                )
            )
        self._transact_write_with_chunking(transact_items)
