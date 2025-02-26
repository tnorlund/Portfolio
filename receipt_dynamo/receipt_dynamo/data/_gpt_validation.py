# _gpt_validation.py
from typing import Optional, List, Tuple, Dict, Union
from receipt_dynamo import GPTValidation, itemToGPTValidation
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _GPTValidation:
    """
    A class used to represent GPTValidation records in the database.

    This class encapsulates methods to add, update, retrieve, delete,
    and list GPTValidation items in DynamoDB.

    Methods
    -------
    addGPTValidation(validation: GPTValidation)
        Adds a GPTValidation record to the database.
    addGPTValidations(validations: List[GPTValidation])
        Adds multiple GPTValidation records in batches.
    getGPTValidation(image_id: str, receipt_id: int) -> GPTValidation
        Retrieves a single GPTValidation record by its composite key.
    updateGPTValidation(validation: GPTValidation)
        Updates an existing GPTValidation record.
    deleteGPTValidation(validation: GPTValidation)
        Deletes a GPTValidation record.
    listGPTValidations() -> List[GPTValidation]
        Lists all GPTValidation records in the database.
    """

    def addGPTValidation(self, validation: GPTValidation):
        """
        Adds a GPTValidation record to the database.

        Args:
            validation (GPTValidation): The GPTValidation record to add.

        Raises:
            ValueError: If a record with the same key already exists.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=validation.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"GPTValidation already exists: {validation}")
            else:
                raise Exception(f"Error adding GPTValidation: {e}")

    def addGPTValidations(self, validations: List[GPTValidation]):
        """
        Adds multiple GPTValidation records to the database in batches.

        Args:
            validations (List[GPTValidation]): A list of GPTValidation records to add.

        Raises:
            ValueError: If an error occurs during batch writing.
        """
        try:
            for i in range(0, len(validations), CHUNK_SIZE):
                chunk = validations[i : i + CHUNK_SIZE]
                request_items = [{"PutRequest": {"Item": v.to_item()}} for v in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle any unprocessed items by retrying.
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error adding GPTValidations: {e}")

    def getGPTValidation(self, image_id: str, receipt_id: int) -> GPTValidation:
        """
        Retrieves a GPTValidation record from the database by its composite key.

        Args:
            image_id (str): The image ID.
            receipt_id (int): The receipt ID.

        Returns:
            GPTValidation: The retrieved GPTValidation record.

        Raises:
            ValueError: If the record is not found.
        """
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id:05d}#QUERY#VALIDATION"},
        }
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=key,
            )
            if "Item" not in response:
                raise ValueError(f"GPTValidation record not found for key: {key}")
            return itemToGPTValidation(response["Item"])
        except ClientError as e:
            raise Exception(f"Error retrieving GPTValidation: {e}")

    def updateGPTValidation(self, validation: GPTValidation):
        """
        Updates an existing GPTValidation record in the database.

        Args:
            validation (GPTValidation): The GPTValidation record to update.

        Raises:
            ValueError: If the record does not exist.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=validation.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"GPTValidation record not found: {validation}")
            else:
                raise Exception(f"Error updating GPTValidation: {e}")

    def deleteGPTValidation(self, validation: GPTValidation):
        """
        Deletes a GPTValidation record from the database.

        Args:
            validation (GPTValidation): The GPTValidation record to delete.

        Raises:
            ValueError: If the record does not exist.
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=validation.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"GPTValidation record not found: {validation}")
            else:
                raise Exception(f"Error deleting GPTValidation: {e}")

    def deleteGPTValidations(self, validations: List[GPTValidation]):
        """
        Deletes multiple GPTValidation records from the database in batches.

        Args:
            validations (List[GPTValidation]): A list of GPTValidation records to delete.

        Raises:
            ValueError: If an error occurs during batch deletion.
        """
        try:
            for i in range(0, len(validations), CHUNK_SIZE):
                chunk = validations[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": validation.key()}} for validation in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error deleting GPTValidations: {e}")

    def listGPTValidations(
        self, limit: Optional[int] = None, lastEvaluatedKey: Optional[Dict] = None
    ) -> Tuple[List[GPTValidation], Optional[Dict]]:
        """
        Lists GPTValidation records from the database via a global secondary index.
        The query uses the GSITYPE index on the "TYPE" attribute where the value is "GPT_VALIDATION".

        If 'limit' is provided, a single query up to that many items is returned,
        along with a LastEvaluatedKey for pagination if more records remain.
        If 'limit' is None, all GPTValidation records are retrieved by paginating until exhausted.

        Parameters
        ----------
        limit : int, optional
            The maximum number of GPTValidation records to return in one query.
        lastEvaluatedKey : dict, optional
            The key from which to continue a previous paginated query.

        Returns
        -------
        tuple
            A tuple containing:
            1) A list of GPTValidation records.
            2) The LastEvaluatedKey (dict) if more items remain; otherwise, None.

        Raises
        ------
        Exception
            If there is an error querying the database.
        """
        validations = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "GPT_VALIDATION"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validations.extend(
                [itemToGPTValidation(item) for item in response["Items"]]
            )
            if limit is None:
                # If no limit is provided, continue paginating until all items are retrieved.
                while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    validations.extend(
                        [itemToGPTValidation(item) for item in response["Items"]]
                    )
                lek = None
            else:
                lek = response.get("LastEvaluatedKey", None)

            return validations, lek
        except Exception as e:
            raise Exception(f"Error listing GPTValidations: {e}")
