# _gpt_initial_tagging.py
from typing import List
from dynamo.entities.gpt_initial_tagging import GPTInitialTagging, itemToGPTInitialTagging
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25

class _GPTInitialTagging:
    """
    A class used to represent GPTInitialTagging records in the database.

    This class encapsulates methods to add, update, retrieve, delete,
    and list GPTInitialTagging items in DynamoDB. With this design,
    only one record per (image_id, receipt_id) exists.
    """

    def addGPTInitialTagging(self, tagging: GPTInitialTagging):
        """
        Adds a GPTInitialTagging record to the database.

        Args:
            tagging (GPTInitialTagging): The GPTInitialTagging record to add.

        Raises:
            ValueError: If a record with the same key already exists.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=tagging.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"GPTInitialTagging already exists: {tagging}")
            else:
                raise Exception(f"Error adding GPTInitialTagging: {e}")

    def addGPTInitialTaggings(self, taggings: List[GPTInitialTagging]):
        """
        Adds multiple GPTInitialTagging records to the database in batches.

        Args:
            taggings (List[GPTInitialTagging]): A list of GPTInitialTagging records to add.

        Raises:
            ValueError: If an error occurs during batch writing.
        """
        try:
            for i in range(0, len(taggings), CHUNK_SIZE):
                chunk = taggings[i : i + CHUNK_SIZE]
                request_items = [{"PutRequest": {"Item": t.to_item()}} for t in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle any unprocessed items by retrying.
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error adding GPTInitialTaggings: {e}")

    def getGPTInitialTagging(self, image_id: str, receipt_id: int) -> GPTInitialTagging:
        """
        Retrieves a GPTInitialTagging record from the database by its composite key.

        Args:
            image_id (str): The image ID.
            receipt_id (int): The receipt ID.

        Returns:
            GPTInitialTagging: The retrieved GPTInitialTagging record.

        Raises:
            ValueError: If the record is not found.
        """
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id:05d}#QUERY#INITIAL_TAGGING"},
        }
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=key,
            )
            if "Item" not in response:
                raise ValueError(f"GPTInitialTagging record not found for key: {key}")
            return itemToGPTInitialTagging(response["Item"])
        except ClientError as e:
            raise Exception(f"Error retrieving GPTInitialTagging: {e}")

    def updateGPTInitialTagging(self, tagging: GPTInitialTagging):
        """
        Updates an existing GPTInitialTagging record in the database.

        Args:
            tagging (GPTInitialTagging): The GPTInitialTagging record to update.

        Raises:
            ValueError: If the record does not exist.
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=tagging.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"GPTInitialTagging record not found: {tagging}")
            else:
                raise Exception(f"Error updating GPTInitialTagging: {e}")

    def deleteGPTInitialTagging(self, tagging: GPTInitialTagging):
        """
        Deletes a GPTInitialTagging record from the database.

        Args:
            tagging (GPTInitialTagging): The GPTInitialTagging record to delete.

        Raises:
            ValueError: If the record does not exist.
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=tagging.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"GPTInitialTagging record not found: {tagging}")
            else:
                raise Exception(f"Error deleting GPTInitialTagging: {e}")

    def deleteGPTInitialTaggings(self, taggings: List[GPTInitialTagging]):
        """
        Deletes multiple GPTInitialTagging records from the database in batches.

        Args:
            taggings (List[GPTInitialTagging]): A list of GPTInitialTagging records to delete.

        Raises:
            ValueError: If an error occurs during batch deletion.
        """
        try:
            for i in range(0, len(taggings), CHUNK_SIZE):
                chunk = taggings[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": tagging.key()}} for tagging in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(f"Error deleting GPTInitialTaggings: {e}")

    def listGPTInitialTaggings(self) -> List[GPTInitialTagging]:
        """
        Lists all GPTInitialTagging records in the database.

        Returns:
            List[GPTInitialTagging]: A list of GPTInitialTagging records.

        Raises:
            Exception: If there is an error querying the database.
        """
        taggings = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSITYPE",
                KeyConditionExpression="#t = :val",
                ExpressionAttributeNames={"#t": "TYPE"},
                ExpressionAttributeValues={":val": {"S": "GPT_INITIAL_TAGGING"}},
            )
            taggings.extend([itemToGPTInitialTagging(item) for item in response["Items"]])
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSITYPE",
                    KeyConditionExpression="#t = :val",
                    ExpressionAttributeNames={"#t": "TYPE"},
                    ExpressionAttributeValues={":val": {"S": "GPT_INITIAL_TAGGING"}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                taggings.extend([itemToGPTInitialTagging(item) for item in response["Items"]])
            return taggings
        except Exception as e:
            raise Exception(f"Error listing GPTInitialTaggings: {e}")