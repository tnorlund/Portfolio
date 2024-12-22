import boto3
from dynamo.data._image import _Image
from dynamo.entities import Image


class DynamoClient(_Image):
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
