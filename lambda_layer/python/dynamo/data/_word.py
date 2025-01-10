from dynamo import Word, itemToWord
from botocore.exceptions import ClientError

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Word:
    """
    A class used to represent a Word in the database.

    Methods
    -------
    addWord(word: Word)
        Adds a word to the database.

    """

    def addWord(self, word: Word):
        """Adds a word to the database

        Args:
            word (Word): The word to add to the database

        Raises:
            ValueError: When a word with the same ID already
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Word with ID {word.id} already exists")

    def addWords(self, words: list[Word]):
        """Adds a list of words to the database

        Args:
            words (list[Word]): The words to add to the database

        Raises:
            ValueError: When a word with the same ID already
        """
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": word.to_item()}} for word in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not add words to the database")
        
    def updateWord(self, word: Word):
        """Updates a word in the database

        Args:
            word (Word): The word to update in the database

        Raises:
            ValueError: When a word with the same ID does not exist
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Word with ID {word.id} not found")
            else:
                raise Exception(f"Error updating word: {e}")

    def deleteWord(self, image_id: int, line_id: int, word_id: int):
        """Deletes a word from the database

        Args:
            image_id (int): The ID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to delete
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Word with ID {word_id} not found")
    
    def deleteWords(self, words: list[Word]):
        """Deletes a list of words from the database"""
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": word.key()}} for word in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError("Could not delete words from the database")

    def deleteWordsFromLine(self, image_id: int, line_id: int):
        """Deletes all words from a line

        Args:
            image_id (int): The ID of the image the line belongs to
            line_id (int): The ID of the line to delete words from
        """
        words = self.listWordsFromLine(image_id, line_id)
        self.deleteWords(words)

    def getWord(self, image_id: int, line_id: int, word_id: int) -> Word:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}"},
                },
            )
            return itemToWord(response["Item"])
        except KeyError:
            raise ValueError(f"Word with ID {word_id} not found")

    def listWords(self) -> list[Word]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "Type": {
                    "AttributeValueList": [{"S": "WORD"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [itemToWord(item) for item in response["Items"]]

    def listWordsFromLine(self, image_id: int, line_id: int) -> list[Word]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "PK": {
                    "AttributeValueList": [{"S": f"IMAGE#{image_id:05d}"}],
                    "ComparisonOperator": "EQ",
                },
                "SK": {
                    "AttributeValueList": [{"S": f"LINE#{line_id:05d}"}],
                    "ComparisonOperator": "BEGINS_WITH",
                },
            },
        )
        return [itemToWord(item) for item in response["Items"]]
