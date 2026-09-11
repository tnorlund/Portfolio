from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

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
    @handle_dynamodb_errors("claim_ocr_routing_decision")
    def claim_ocr_routing_decision(
        self,
        image_id: str,
        job_id: str,
        owner: str,
        *,
        now: datetime,
        lease_seconds: int = 960,
    ) -> Literal["claimed", "completed", "busy"]:
        """Claim one correction attempt without overlapping its redeliveries.

        The default lease exceeds Lambda's absolute 900-second runtime limit.
        Callers outside Lambda must enforce that same maximum runtime before
        relying on expired-lease takeover to serialize destructive work.
        """
        self._validate_image_id(image_id)
        assert_valid_uuid(job_id)
        self._validate_routing_owner(owner)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise EntityValidationError("now must be timezone-aware")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 900
        ):
            raise EntityValidationError("lease_seconds must exceed 900")
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"ROUTING#{job_id}"},
        }
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key=key,
                UpdateExpression=(
                    "SET lease_owner = :owner, lease_expires_at = :expires"
                ),
                ConditionExpression=(
                    "attribute_exists(PK) AND #status <> :completed AND "
                    "(attribute_not_exists(lease_owner) OR "
                    "lease_expires_at <= :now)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":owner": {"S": owner},
                    ":completed": {"S": "COMPLETED"},
                    ":now": {"N": str(now.timestamp())},
                    ":expires": {
                        "N": str(
                            (
                                now + timedelta(seconds=lease_seconds)
                            ).timestamp()
                        )
                    },
                },
            )
        except ClientError as exc:
            if (
                exc.response["Error"]["Code"]
                != "ConditionalCheckFailedException"
            ):
                raise
            response = self._client.get_item(
                TableName=self.table_name, Key=key, ConsistentRead=True
            )
            item = response.get("Item")
            if item is None:
                raise EntityNotFoundError(
                    "OCR routing decision not found"
                ) from exc
            if item["status"]["S"] == "COMPLETED":
                return "completed"
            return "busy"
        return "claimed"

    @staticmethod
    def _validate_routing_owner(owner: str) -> None:
        if not isinstance(owner, str) or not owner:
            raise EntityValidationError("owner must be a non-empty string")

    @handle_dynamodb_errors("complete_ocr_routing_decision")
    def complete_ocr_routing_decision(
        self, decision: OCRRoutingDecision, owner: str
    ) -> None:
        """Publish completion only for the current, unexpired claim owner."""
        if not isinstance(decision, OCRRoutingDecision):
            raise EntityValidationError("decision must be OCRRoutingDecision")
        self._validate_routing_owner(owner)
        item = decision.to_item()
        if decision.status != "COMPLETED":
            raise EntityValidationError("decision must be COMPLETED")
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key=decision.key,
                UpdateExpression=(
                    "SET #status = :completed, receipt_count = :count, "
                    "updated_at = :updated, GSI1PK = :gsi "
                    "REMOVE lease_owner, lease_expires_at"
                ),
                ConditionExpression=(
                    "lease_owner = :owner AND lease_expires_at > :now "
                    "AND #status <> :completed"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":owner": {"S": owner},
                    ":now": {"N": str(datetime.now(timezone.utc).timestamp())},
                    ":completed": {"S": "COMPLETED"},
                    ":count": item["receipt_count"],
                    ":updated": item["updated_at"],
                    ":gsi": item["GSI1PK"],
                },
            )
        except ClientError as exc:
            if (
                exc.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise OperationError(
                    "OCR routing lease is no longer owned"
                ) from exc
            raise

    @handle_dynamodb_errors("release_ocr_routing_decision")
    def release_ocr_routing_decision(
        self, image_id: str, job_id: str, owner: str
    ) -> bool:
        """Release failed work without altering another owner's completion."""
        self._validate_image_id(image_id)
        assert_valid_uuid(job_id)
        self._validate_routing_owner(owner)
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"ROUTING#{job_id}"},
                },
                UpdateExpression="REMOVE lease_owner, lease_expires_at",
                ConditionExpression=(
                    "lease_owner = :owner AND #status <> :completed"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":owner": {"S": owner},
                    ":completed": {"S": "COMPLETED"},
                },
            )
        except ClientError as exc:
            if (
                exc.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                return False
            raise
        return True

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
        self._add_entity(
            ocr_routing_decision,
            condition_expression="attribute_not_exists(PK)",
        )

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
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=decision.to_item())
            )
            for decision in ocr_routing_decisions
        ]
        self._batch_write_with_retry(request_items)

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

        self._update_entity(
            ocr_routing_decision,
            condition_expression="attribute_exists(PK)",
        )

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
        self._delete_entity(
            ocr_routing_decision,
            condition_expression="attribute_exists(PK)",
        )

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
