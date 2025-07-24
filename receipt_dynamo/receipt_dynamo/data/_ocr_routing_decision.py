from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import (
    DynamoClientProtocol,
    DeleteTypeDef,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    OperationError,
)
from receipt_dynamo.entities.ocr_routing_decision import (
    OCRRoutingDecision,
    item_to_ocr_routing_decision,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


class _OCRRoutingDecision(DynamoClientProtocol):
    def add_ocr_routing_decision(
        self, ocr_routing_decision: OCRRoutingDecision
    ):
        if ocr_routing_decision is None:
            raise ValueError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise ValueError(
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
                raise ValueError(
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

    def add_ocr_routing_decisions(
        self, ocr_routing_decisions: list[OCRRoutingDecision]
    ):
        if ocr_routing_decisions is None:
            raise ValueError("ocr_routing_decisions cannot be None")
        if not isinstance(ocr_routing_decisions, list):
            raise ValueError("ocr_routing_decisions must be a list")
        if not all(
            isinstance(decision, OCRRoutingDecision)
            for decision in ocr_routing_decisions
        ):
            raise ValueError(
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

    def update_ocr_routing_decision(
        self, ocr_routing_decision: OCRRoutingDecision
    ):
        if ocr_routing_decision is None:
            raise ValueError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise ValueError(
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
                raise ValueError(
                    f"OCR routing decision for Image ID "
                    f"'{ocr_routing_decision.image_id}' and Job ID "
                    f"'{ocr_routing_decision.job_id}' not found"
                ) from e
            raise OperationError(
                f"Error updating OCR routing decision: {e}"
            ) from e

    def get_ocr_routing_decision(
        self, image_id: str, job_id: str
    ) -> OCRRoutingDecision:
        if image_id is None:
            raise ValueError("image_id cannot be None")
        if job_id is None:
            raise ValueError("job_id cannot be None")
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        if not isinstance(job_id, str):
            raise ValueError("job_id must be a string")
        assert_valid_uuid(image_id)
        assert_valid_uuid(job_id)
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"ROUTING#{job_id}"},
                },
            )
            if "Item" in response:
                return item_to_ocr_routing_decision(response["Item"])
            else:
                raise ValueError(
                    f"OCR routing decision for Image ID '{image_id}' "
                    f"and Job ID '{job_id}' not found"
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ValueError(
                    f"OCR routing decision for Image ID '{image_id}' "
                    f"and Job ID '{job_id}' not found"
                ) from e
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            if error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            raise OperationError(
                f"Error getting OCR routing decision: {e}"
            ) from e

    def delete_ocr_routing_decision(
        self, ocr_routing_decision: OCRRoutingDecision
    ):
        if ocr_routing_decision is None:
            raise ValueError("ocr_routing_decision cannot be None")
        if not isinstance(ocr_routing_decision, OCRRoutingDecision):
            raise ValueError(
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
                raise ValueError(
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
            raise ValueError("ocr_routing_decisions cannot be None")
        if not isinstance(ocr_routing_decisions, list):
            raise ValueError("ocr_routing_decisions must be a list")
        if not all(
            isinstance(decision, OCRRoutingDecision)
            for decision in ocr_routing_decisions
        ):
            raise ValueError(
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
                    raise ValueError(
                        "OCR routing decision does not exist"
                    ) from e
                if error_code == "ProvisionedThroughputExceededException":
                    raise RuntimeError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                if error_code == "InternalServerError":
                    raise RuntimeError(f"Internal server error: {e}") from e
                if error_code == "AccessDeniedException":
                    raise RuntimeError(f"Access denied: {e}") from e
                raise RuntimeError(
                    f"Error deleting OCR routing decisions: {e}"
                ) from e
