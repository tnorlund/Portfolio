from dynamo import Word, itemToWord
from botocore.exceptions import ClientError


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
