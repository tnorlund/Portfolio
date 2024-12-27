import boto3
from dynamo.data._image import _Image
from dynamo.data._line import _Line
from dynamo.data._word import _Word
from dynamo.data._letter import _Letter
from dynamo.data._scaled_image import _ScaledImage

class DynamoClient(_Image, _Line, _Word, _Letter, _ScaledImage):
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
