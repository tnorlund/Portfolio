import boto3
from dynamo.data._image import _Image
from dynamo.entities import Image


class DynamoClient(_Image):
    def __init__(self, table_name: str, region: str = "us-east-1"):
        self._client = boto3.client("dynamodb", region_name=region)
        self.table_name = table_name

