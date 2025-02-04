# _gpt_validation.py
from typing import Optional, List, Tuple, Dict, Union
from dynamo.entities.gpt_validation import GPTValidation, itemToGPTValidation
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
    getGPTValidation(image_id: str, receipt_id: int, line_id: int, word_id: int, tag: str) -> GPTValidation
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

    def getGPTValidation(
        self, image_id: str, receipt_id: int, line_id: int, word_id: int, tag: str
    ) -> GPTValidation:
        """
        Retrieves a GPTValidation record from the database by its composite key.

        Args:
            image_id (str): The image ID.
            receipt_id (int): The receipt ID.
            line_id (int): The line ID.
            word_id (int): The word ID.
            tag (str): The tag associated with the validation.

        Returns:
            GPTValidation: The retrieved GPTValidation record.

        Raises:
            ValueError: If the record is not found.
        """
        # Construct the key exactly as in GPTValidation.key().
        # (Assuming GPTValidation uses fixed-width formatting for the tag.)
        spaced_tag = f"{tag:_>40}"
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{receipt_id:05d}"
                    f"#LINE#{line_id:05d}"
                    f"#WORD#{word_id:05d}"
                    f"#TAG#{spaced_tag}"
                    f"#QUERY#VALIDATION"
                )
            },
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

    def listGPTValidations(self) -> List[GPTValidation]:
        """
        Lists all GPTValidation records in the database.

        Returns:
            List[GPTValidation]: A list of GPTValidation records.

        Raises:
            Exception: If there is an error querying the database.
        """
        validations = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",  # Assumes you have a GSI indexing on TYPE.
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "GPT_VALIDATION"}},
            )
            validations.extend(
                [itemToGPTValidation(item) for item in response["Items"]]
            )
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "GPT_VALIDATION"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                validations.extend(
                    [itemToGPTValidation(item) for item in response["Items"]]
                )
            return validations
        except Exception as e:
            raise Exception(f"Error listing GPTValidations: {e}")
