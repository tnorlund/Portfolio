from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    OperationError,
)
from receipt_dynamo.entities.ocr_routing_decision import (
    OCRRoutingDecision,
    item_to_ocr_routing_decision,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


class _OCRRoutingDecision(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    @handle_dynamodb_errors("add_ocr_routing_decision")
    def add_ocr_routing_decision(
        self, ocr_routing_decision: OCRRoutingDecision
    ):
        if ocr_routing_decision is None:
            raise EntityValidationError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise EntityValidationError(
                "ocr_routing_decision must be an instance of "
                "OCRRoutingDecision"
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=ocr_routing_decision.to_item(),
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise EntityAlreadyExistsError(
                    f"OCR routing decision for Image ID "
                    f"'{ocr_routing_decision.image_id}' already exists"
                ) from e
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add OCR routing decision to DynamoDB: {e}"
                ) from e
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            if error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            raise OperationError(
                f"Error adding OCR routing decision: {e}"
            ) from e

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

        for i in range(0, len(ocr_routing_decisions), 25):
            chunk = ocr_routing_decisions[i : i + 25]
            request_items = [
                WriteRequestTypeDef(
                    PutRequest=PutRequestTypeDef(Item=decision.to_item())
                )
                for decision in chunk
            ]
            try:
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
            unprocessed = response.get("UnprocessedItems", {})
            while unprocessed.get(self.table_name):
                try:
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if error_code == "ProvisionedThroughputExceededException":
                        raise DynamoDBThroughputError(
                            f"Provisioned throughput exceeded: {e}"
                        ) from e

    @handle_dynamodb_errors("update_ocr_routing_decision")
    def update_ocr_routing_decision(
        self, ocr_routing_decision: OCRRoutingDecision
    ):
        if ocr_routing_decision is None:
            raise EntityValidationError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise EntityValidationError(
                "ocr_routing_decision must be an instance of "
                "OCRRoutingDecision"
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=ocr_routing_decision.to_item(),
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise EntityNotFoundError(
                    f"OCR routing decision for Image ID "
                    f"'{ocr_routing_decision.image_id}' and Job ID "
                    f"'{ocr_routing_decision.job_id}' not found"
            ) from e
            raise OperationError(
                f"Error updating OCR routing decision: {e}"
            ) from e

    @handle_dynamodb_errors("get_ocr_routing_decision")
    def get_ocr_routing_decision(
        self, image_id: str, job_id: str
    ) -> OCRRoutingDecision:
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        if not isinstance(image_id, str):
            raise EntityValidationError("image_id must be a string")
        if not isinstance(job_id, str):
            raise EntityValidationError("job_id must be a string")
        assert_valid_uuid(image_id)
        assert_valid_uuid(job_id)
        
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"ROUTING#{job_id}",
            entity_class=OCRRoutingDecision,
            converter_func=item_to_ocr_routing_decision
        )
        
        if result is None:
            raise EntityNotFoundError(
                f"OCR routing decision for Image ID '{image_id}' "
                f"and Job ID '{job_id}' not found"
            )
        
        return result

    @handle_dynamodb_errors("delete_ocr_routing_decision")
    def delete_ocr_routing_decision(
        self, ocr_routing_decision: OCRRoutingDecision
    ):
        if ocr_routing_decision is None:
            raise EntityValidationError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise EntityValidationError(
                "ocr_routing_decision must be an instance of "
                "OCRRoutingDecision"
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{ocr_routing_decision.image_id}"},
                    "SK": {"S": f"ROUTING#{ocr_routing_decision.job_id}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise EntityNotFoundError(
                    f"OCR routing decision for Image ID "
                    f"'{ocr_routing_decision.image_id}' and Job ID "
                    f"'{ocr_routing_decision.job_id}' does not exist."
            ) from e
            raise OperationError(
                f"Error deleting OCR routing decision: {e}"
            ) from e

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
                "All ocr_routing_decisions must be instances of "
                "OCRRoutingDecision"
            )
        for i in range(0, len(ocr_routing_decisions), 25):
            chunk = ocr_routing_decisions[i : i + 25]
            transact_items = []
            for item in chunk:
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
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise EntityNotFoundError(
                        "OCR routing decision does not exist"
            ) from e
                if error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                if error_code == "InternalServerError":
                    raise DynamoDBServerError(f"Internal server error: {e}") from e
                if error_code == "AccessDeniedException":
                    raise DynamoDBAccessError(f"Access denied: {e}") from e
                raise DynamoDBError(
                    f"Error deleting OCR routing decisions: {e}"
                ) from e
