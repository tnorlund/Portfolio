from botocore.exceptions import ClientError

from receipt_dynamo.constants import EmbeddingStatus, SectionType
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    OperationError,
)

# Fix circular import by importing directly from the entity module
from receipt_dynamo.entities.receipt_section import (
    ReceiptSection,
    item_to_receipt_section,
)
from receipt_dynamo.entities.util import assert_valid_uuid

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptSection(DynamoClientProtocol):
    """
    A class used to represent a ReceiptSection in the database.

    Methods
    -------
    add_receipt_section(section: ReceiptSection)
        Adds a single ReceiptSection.
    add_receipt_sections(sections: list[ReceiptSection])
        Adds multiple ReceiptSections.
    update_receipt_section(section: ReceiptSection)
        Updates a ReceiptSection.
    update_receipt_sections(sections: list[ReceiptSection])
        Updates multiple ReceiptSections.
    delete_receipt_section(receipt_id: int, image_id: str, section_type: str)
        Deletes a single ReceiptSection by IDs.
    delete_receipt_sections(sections: list[ReceiptSection])
        Deletes multiple ReceiptSections.
    get_receipt_section(receipt_id: int, image_id: str, section_type: str) -> ReceiptSection
        Retrieves a single ReceiptSection by IDs.
    list_receipt_sections() -> list[ReceiptSection]
        Returns all ReceiptSections from the table.
    """

    def add_receipt_section(self, section: ReceiptSection):
        """Adds a single ReceiptSection to DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=section.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptSection with receipt_id {section.receipt_id}, image_id {section.image_id}, and section_type {section.section_type} already exists"
                )
            else:
                raise

    def add_receipt_sections(self, sections: list[ReceiptSection]):
        """Adds multiple ReceiptSections to DynamoDB in batches of CHUNK_SIZE."""
        if sections is None:
            raise ValueError("sections parameter is required and cannot be None.")
        if not isinstance(sections, list):
            raise ValueError("sections must be a list of ReceiptSection instances.")
        if not all(isinstance(s, ReceiptSection) for s in sections):
            raise ValueError(
                "All sections must be instances of the ReceiptSection class."
            )
        try:
            for i in range(0, len(sections), CHUNK_SIZE):
                chunk = sections[i : i + CHUNK_SIZE]
                request_items = [{"PutRequest": {"Item": s.to_item()}} for s in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not add ReceiptSections to the database") from e

    def update_receipt_section(self, section: ReceiptSection):
        """Updates an existing ReceiptSection in DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=section.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptSection with receipt_id {section.receipt_id}, image_id {section.image_id}, and section_type {section.section_type} does not exist"
                )
            else:
                raise

    def update_receipt_sections(self, sections: list[ReceiptSection]):
        """Updates multiple existing ReceiptSections in DynamoDB."""
        if sections is None:
            raise ValueError("sections parameter is required and cannot be None.")
        if not isinstance(sections, list):
            raise ValueError("sections must be a list of ReceiptSection instances.")
        if not all(isinstance(s, ReceiptSection) for s in sections):
            raise ValueError(
                "All sections must be instances of the ReceiptSection class."
            )
        for i in range(0, len(sections), CHUNK_SIZE):
            chunk = sections[i : i + CHUNK_SIZE]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": s.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for s in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError("One or more ReceiptSections do not exist")
                elif error_code == "ProvisionedThroughputExceededException":
                    raise ValueError("Provisioned throughput exceeded")
                elif error_code == "InternalServerError":
                    raise ValueError("Internal server error")
                elif error_code == "ValidationException":
                    raise ValueError("One or more parameters given were invalid")
                elif error_code == "AccessDeniedException":
                    raise ValueError("Access denied")
                else:
                    raise ValueError(
                        f"Could not update ReceiptSections in the database: {e}"
                    )

    def delete_receipt_section(self, receipt_id: int, image_id: str, section_type: str):
        """Deletes a single ReceiptSection by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#SECTION#{section_type}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptSection with receipt_id {receipt_id}, image_id {image_id}, and section_type {section_type} not found"
                )
            else:
                raise

    def delete_receipt_sectionsion(self, sections: list[ReceiptSection]):
        """Deletes multiple ReceiptSections in batch."""
        try:
            for i in range(0, len(sections), CHUNK_SIZE):
                chunk = sections[i : i + CHUNK_SIZE]
                request_items = [{"DeleteRequest": {"Key": s.key()}} for s in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(
                "Could not delete ReceiptSections from the database"
            ) from e

    def get_receipt_section(
        self, receipt_id: int, image_id: str, section_type: str
    ) -> ReceiptSection:
        """Retrieves a single ReceiptSection by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#SECTION#{section_type}"},
                },
            )
            return item_to_receipt_section(response["Item"])
        except KeyError:
            raise ValueError(
                f"ReceiptSection with receipt_id {receipt_id}, image_id {image_id}, and section_type {section_type} not found"
            )

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptSection]:
        """Retrieves all ReceiptSections for a given receipt."""
        if image_id is None:
            raise ValueError("image_id is required")
        if receipt_id is None:
            raise ValueError("receipt_id is required")
        try:
            # Query by the image ID for the PK and
            expected_pk = f"IMAGE#{image_id}"
            start_of_sk = f"RECEIPT#{receipt_id:05d}#SECTION#"
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk and begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": expected_pk},
                    ":sk": {"S": start_of_sk},
                },
            )
            return [item_to_receipt_section(item) for item in response["Items"]]
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ValueError(
                    f"Could not get ReceiptSections from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError(f"Provisioned throughput exceeded: {e}") from e
            else:
                raise ValueError(
                    f"Could not get ReceiptSections from DynamoDB: {e}"
                ) from e

    def list_receipt_sections(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptSection], dict | None]:
        """Returns all ReceiptSections from the table with optional pagination."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        receipt_sections = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_SECTION"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            receipt_sections.extend(
                [item_to_receipt_section(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt sections
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    receipt_sections.extend(
                        [item_to_receipt_section(item) for item in response["Items"]]
                    )
                # No further pages left. LEK is None.
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_sections, last_evaluated_key

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt sections from DynamoDB: {e}"
                )
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(f"Provisioned throughput exceeded: {e}")
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}")
            else:
                raise OperationError(f"Error listing receipt sections: {e}")
