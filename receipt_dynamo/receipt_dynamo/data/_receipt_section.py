# infra/lambda_layer/python/dynamo/data/_receipt_section.py
from typing import TYPE_CHECKING, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    QueryInputTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    OperationError,
)
from receipt_dynamo.entities.receipt_section import (
    ReceiptSection,
    item_to_receipt_section,
)

if TYPE_CHECKING:
    pass

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptSection(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class providing methods to interact with "ReceiptSection" entities in
    DynamoDB.
    This class is typically used within a DynamoClient to access and manage
    receipt section records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_section(section: ReceiptSection):
        Adds a single ReceiptSection.
    add_receipt_sections(sections: list[ReceiptSection]):
        Adds multiple ReceiptSections.
    update_receipt_section(section: ReceiptSection):
        Updates a ReceiptSection.
    update_receipt_sections(sections: list[ReceiptSection]):
        Updates multiple ReceiptSections.
    delete_receipt_section(receipt_id: int, image_id: str, section_type: str):
        Deletes a single ReceiptSection by IDs.
    delete_receipt_sections(sections: list[ReceiptSection]):
        Deletes multiple ReceiptSections.
    get_receipt_section(receipt_id: int, image_id: str, section_type: str)
        -> ReceiptSection:
        Retrieves a single ReceiptSection by IDs.
    get_receipt_sections_from_receipt(image_id: str, receipt_id: int)
        -> list[ReceiptSection]:
        Retrieves all ReceiptSections for a given receipt.
    list_receipt_sections(...) -> tuple[list[ReceiptSection], dict | None]:
        Returns all ReceiptSections from the table with pagination.
    """

    @handle_dynamodb_errors("add_receipt_section")
    def add_receipt_section(self, section: ReceiptSection) -> None:
        """
        Adds a single ReceiptSection to DynamoDB.

        Parameters
        ----------
        section : ReceiptSection
            The ReceiptSection to add.

        Raises
        ------
        ValueError
            If the section already exists.
        """
        self._validate_entity(section, ReceiptSection, "section")
        self._add_entity(section)

    @handle_dynamodb_errors("add_receipt_sections")
    def add_receipt_sections(self, sections: list[ReceiptSection]) -> None:
        """
        Adds multiple ReceiptSections to DynamoDB in batches.

        Parameters
        ----------
        sections : list[ReceiptSection]
            The ReceiptSections to add.

        Raises
        ------
        ValueError
            If sections is invalid.
        """
        self._validate_entity_list(sections, ReceiptSection, "sections")

        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=s.to_item()))
            for s in sections
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_section")
    def update_receipt_section(self, section: ReceiptSection) -> None:
        """
        Updates an existing ReceiptSection in DynamoDB.

        Parameters
        ----------
        section : ReceiptSection
            The ReceiptSection to update.

        Raises
        ------
        ValueError
            If the section does not exist.
        """
        self._validate_entity(section, ReceiptSection, "section")
        self._update_entity(section)

    @handle_dynamodb_errors("update_receipt_sections")
    def update_receipt_sections(self, sections: list[ReceiptSection]) -> None:
        """
        Updates multiple existing ReceiptSections in DynamoDB.

        Parameters
        ----------
        sections : list[ReceiptSection]
            The ReceiptSections to update.

        Raises
        ------
        ValueError
            If sections is invalid or if any section does not exist.
        """
        self._update_entities(sections, ReceiptSection, "sections")

    def delete_receipt_section(
        self, receipt_id: int, image_id: str, section_type: str
    ) -> None:
        """
        Deletes a single ReceiptSection by IDs.

        Parameters
        ----------
        receipt_id : int
            The receipt ID.
        image_id : str
            The image ID.
        section_type : str
            The section type.

        Raises
        ------
        ValueError
            If the section is not found.
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#SECTION#{section_type}"
                    },
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise EntityNotFoundError(
                    f"ReceiptSection with receipt_id {receipt_id}, "
                    f"image_id {image_id}, and section_type {section_type} "
                    "not found"
            ) from e
            else:
                raise

    def delete_receipt_sections(self, sections: list[ReceiptSection]) -> None:
        """
        Deletes multiple ReceiptSections in batch.

        Parameters
        ----------
        sections : list[ReceiptSection]
            The ReceiptSections to delete.

        Raises
        ------
        ValueError
            If unable to delete sections.
        """
        self._validate_entity_list(sections, ReceiptSection, "sections")

        try:
            for i in range(0, len(sections), CHUNK_SIZE):
                chunk = sections[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=s.key)
                    )
                    for s in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise EntityValidationError(
                "Could not delete ReceiptSections from the database"
            ) from e

    @handle_dynamodb_errors("get_receipt_section")
    def get_receipt_section(
        self, receipt_id: int, image_id: str, section_type: str
    ) -> ReceiptSection:
        """
        Retrieves a single ReceiptSection by IDs.

        Parameters
        ----------
        receipt_id : int
            The receipt ID.
        image_id : str
            The image ID.
        section_type : str
            The section type.

        Returns
        -------
        ReceiptSection
            The retrieved ReceiptSection.

        Raises
        ------
        ValueError
            If the section is not found.
        """
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#SECTION#{section_type}",
            entity_class=ReceiptSection,
            converter_func=item_to_receipt_section
        )
        
        if result is None:
            raise EntityNotFoundError(
                f"ReceiptSection with receipt_id {receipt_id}, "
                f"image_id {image_id}, and section_type {section_type} "
                "not found"
            )
        
        return result

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptSection]:
        """
        Retrieves all ReceiptSections for a given receipt.

        Parameters
        ----------
        image_id : str
            The image ID.
        receipt_id : int
            The receipt ID.

        Returns
        -------
        list[ReceiptSection]
            List of ReceiptSections for the receipt.

        Raises
        ------
        ValueError
            If parameters are invalid or query fails.
        """
        if image_id is None:
            raise EntityValidationError("image_id is required")
        if receipt_id is None:
            raise EntityValidationError("receipt_id is required")
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
            return [
                item_to_receipt_section(item) for item in response["Items"]
            ]
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityValidationError(
                    f"Could not get ReceiptSections from DynamoDB: {e}"
            ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise EntityValidationError(
                    f"Provisioned throughput exceeded: {e}"
            ) from e
            else:
                raise EntityValidationError(
                    f"Could not get ReceiptSections from DynamoDB: {e}"
            ) from e

    def list_receipt_sections(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptSection], dict | None]:
        """
        Returns all ReceiptSections from the table with optional pagination.

        Parameters
        ----------
        limit : int, optional
            Maximum number of items to return.
        last_evaluated_key : dict, optional
            Key to continue pagination from.

        Returns
        -------
        tuple[list[ReceiptSection], dict | None]
            List of ReceiptSections and last evaluated key for pagination.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        receipt_sections = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_SECTION"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            receipt_sections.extend(
                [item_to_receipt_section(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt sections
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    receipt_sections.extend(
                        [
                            item_to_receipt_section(item)
                            for item in response["Items"]
                        ]
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
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise EntityValidationError(
                    f"One or more parameters given were invalid: {e}"
            ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error listing receipt sections: {e}"
                ) from e
