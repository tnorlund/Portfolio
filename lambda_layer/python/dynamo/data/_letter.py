from dynamo import Letter, itemToLetter
from botocore.exceptions import ClientError

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
            raise ValueError(f"Letter with ID {letter.id} already exists")
        
    def getLetter(self, image_id: int, line_id: int, word_id: int, letter_id: int) -> Letter:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id:05d}"},
                    "SK": {"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"},
                },
            )
            return itemToLetter(response["Item"])
        except KeyError:
            raise ValueError(f"Letter with ID {letter_id} not found")
        
    def listLetters(self) -> list[Letter]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "Type": {
                    "AttributeValueList": [{"S": "LETTER"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [itemToLetter(item) for item in response["Items"]]
    
    def listLettersFromWord(self, image_id: int, line_id: int, word_id: int) -> list[Letter]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "Type": {
                    "AttributeValueList": [{"S": "LETTER"}],
                    "ComparisonOperator": "EQ",
                },
                "PK": {
                    "AttributeValueList": [{"S": f"IMAGE#{image_id:05d}"}],
                    "ComparisonOperator": "BEGINS_WITH",
                },
                "SK": {
                    "AttributeValueList": [{"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}"}],
                    "ComparisonOperator": "BEGINS_WITH",
                }
            },
        )
        return [itemToLetter(item) for item in response["Items"]]