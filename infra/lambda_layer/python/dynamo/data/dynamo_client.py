import boto3
from dynamo.data._image import _Image
from dynamo.data._line import _Line
from dynamo.data._word import _Word
from dynamo.data._letter import _Letter
from dynamo.data._receipt import _Receipt
from dynamo.data._receipt_line import _ReceiptLine
from dynamo.data._receipt_word import _ReceiptWord
from dynamo.data._receipt_letter import _ReceiptLetter
from dynamo.data._word_tag import _WordTag
from dynamo.data._receipt_word_tag import _ReceiptWordTag


class DynamoClient(
    _Image,
    _Line,
    _Word,
    _Letter,
    _Receipt,
    _ReceiptLine,
    _ReceiptWord,
    _ReceiptLetter,
    _WordTag,
    _ReceiptWordTag,
):
    """A class used to represent a DynamoDB client."""

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """Initializes a DynamoClient instance.

        Args:
            table_name (str): The name of the DynamoDB table.
            region (str, optional): The AWS region where the DynamoDB table is located. Defaults to "us-east-1".

        Attributes:
            _client (boto3.client): The Boto3 DynamoDB client.
            table_name (str): The name of the DynamoDB table.
        """

        self._client = boto3.client("dynamodb", region_name=region)
        self.table_name = table_name
        # Ensure the table already exists
        try:
            self._client.describe_table(TableName=self.table_name)
        except self._client.exceptions.ResourceNotFoundException:
            raise ValueError(
                f"The table '{self.table_name}' does not exist in region '{region}'."
            )
    
    def gpt_receipt(receipt_id: int):
        pass
