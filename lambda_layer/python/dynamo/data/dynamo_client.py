import boto3
from dynamo.data._image import _Image
from dynamo.entities import Image


class DynamoClient(_Image):
    def __init__(self, table_name: str, region: str = "us-east-1"):
        self._client = boto3.client("dynamodb", region_name=region)
        # self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self.table_name = table_name

    def addImage(self, image: Image):
        self._client.put_item(TableName=self.table_name, Item=image.to_item())
