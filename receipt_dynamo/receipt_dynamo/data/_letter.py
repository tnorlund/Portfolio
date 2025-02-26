from receipt_dynamo import Letter, itemToLetter
from botocore.exceptions import ClientError
from typing import Optional, Dict, Tuple

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Letter:
    """
    A class used to represent a Letter in the database.

    Methods
    -------
    addLetter(letter: Letter)
        Adds a letter to the database.
    """

    def addLetter(self, letter: Letter):
        """Adds a letter to the database

        Args:
            letter (Letter): The letter to add to the database

        Raises:
            ValueError: When a letter with the same ID already exists
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Letter with ID {letter.letter_id} already exists")

    def addLetters(self, letters: list[Letter]):
        """Adds a list of letters to the database

        Args:
            letters (list[Letter]): The letters to add to the database

        Raises:
            ValueError: When a letter with the same ID already exists
        """
        try:
            for i in range(0, len(letters), CHUNK_SIZE):
                chunk = letters[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": letter.to_item()}} for letter in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
        except ClientError as e:
            raise ValueError("Could not add letters to the database")

    def updateLetter(self, letter: Letter):
        """Updates a letter in the database

        Args:
            letter (Letter): The letter to update in the database

        Raises:
            ValueError: When the letter does not exist
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Letter with ID {letter.letter_id} not found")

    def deleteLetter(self, image_id: str, line_id: int, word_id: int, letter_id: int):
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
                    },
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Letter with ID {letter_id} not found")

    def deleteLetters(self, letters: list[Letter]):
        """Deletes a list of letters from the database"""
        try:
            for i in range(0, len(letters), CHUNK_SIZE):
                chunk = letters[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": letter.key()}} for letter in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
        except ClientError as e:
            raise ValueError("Could not delete letters from the database")

    def deleteLettersFromWord(self, image_id: str, line_id: int, word_id: int):
        """Deletes all letters from a word

        Args:
            image_id (str): The UUID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to delete letters from
        """
        letters = self.listLettersFromWord(image_id, line_id, word_id)
        self.deleteLetters(letters)

    def getLetter(
        self, image_id: str, line_id: int, word_id: int, letter_id: int
    ) -> Letter:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
                    },
                },
            )
            return itemToLetter(response["Item"])
        except KeyError:
            raise ValueError(f"Letter with ID {letter_id} not found")

    def listLetters(
        self, limit: Optional[int] = None, last_evaluated_key: Optional[Dict] = None
    ) -> Tuple[list[Letter], Optional[Dict]]:
        """Lists all letters in the database"""
        letters = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "LETTER"}},
                "ScanIndexForward": True,
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            letters.extend([itemToLetter(item) for item in response["Items"]])

            if limit is None:
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    letters.extend([itemToLetter(item) for item in response["Items"]])
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)
            return letters, last_evaluated_key
        except ClientError as e:
            raise ValueError("Could not list letters from the database") from e

    def listLettersFromWord(
        self, image_id: str, line_id: int, word_id: int
    ) -> list[Letter]:
        letters = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#"
                    },
                },
            )
            letters.extend([itemToLetter(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {
                            "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#"
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                letters.extend([itemToLetter(item) for item in response["Items"]])

            return letters
        except ClientError as e:
            raise ValueError("Could not list letters from word") from e
