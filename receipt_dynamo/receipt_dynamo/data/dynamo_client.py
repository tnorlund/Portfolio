import boto3

# Import all the modules needed for multiple inheritance
from receipt_dynamo.data._image import _Image
from receipt_dynamo.data._instance import _Instance
from receipt_dynamo.data._job import _Job
from receipt_dynamo.data._job_checkpoint import _JobCheckpoint
from receipt_dynamo.data._job_dependency import _JobDependency
from receipt_dynamo.data._job_log import _JobLog
from receipt_dynamo.data._job_metric import _JobMetric
from receipt_dynamo.data._job_resource import _JobResource
from receipt_dynamo.data._job_status import _JobStatus
from receipt_dynamo.data._letter import _Letter
from receipt_dynamo.data._line import _Line
from receipt_dynamo.data._places_cache import _PlacesCache
from receipt_dynamo.data._queue import _Queue
from receipt_dynamo.data._receipt import _Receipt
from receipt_dynamo.data._receipt_letter import _ReceiptLetter
from receipt_dynamo.data._receipt_line import _ReceiptLine
from receipt_dynamo.data._receipt_word import _ReceiptWord
from receipt_dynamo.data._receipt_word_tag import _ReceiptWordTag
from receipt_dynamo.data._word import _Word
from receipt_dynamo.data._word_tag import _WordTag
from receipt_dynamo.data._receipt_word_label import _ReceiptWordLabel
from receipt_dynamo.data._receipt_field import _ReceiptField
from receipt_dynamo.data._receipt_label_analysis import _ReceiptLabelAnalysis


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
    _Job,
    _JobStatus,
    _JobResource,
    _JobMetric,
    _JobCheckpoint,
    _JobLog,
    _JobDependency,
    _Queue,
    _Instance,
    _PlacesCache,
    _ReceiptWordLabel,
    _ReceiptField,
    _ReceiptLabelAnalysis,
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
