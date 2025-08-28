from typing import TYPE_CHECKING

import boto3

from receipt_dynamo.data._ai_usage_metric import _AIUsageMetric
from receipt_dynamo.data._batch_summary import _BatchSummary
from receipt_dynamo.data._compaction_lock import _CompactionLock
from receipt_dynamo.data._completion_batch_result import _CompletionBatchResult
from receipt_dynamo.data._embedding_batch_result import _EmbeddingBatchResult
from receipt_dynamo.data._image import _Image
from receipt_dynamo.data._instance import _Instance
from receipt_dynamo.data._job import _Job
from receipt_dynamo.data._job_checkpoint import _JobCheckpoint
from receipt_dynamo.data._job_dependency import _JobDependency
from receipt_dynamo.data._job_log import _JobLog
from receipt_dynamo.data._job_metric import _JobMetric
from receipt_dynamo.data._job_resource import _JobResource
from receipt_dynamo.data._job_status import _JobStatus
from receipt_dynamo.data._label_count_cache import _LabelCountCache
from receipt_dynamo.data._letter import _Letter
from receipt_dynamo.data._line import _Line
from receipt_dynamo.data._ocr_job import _OCRJob
from receipt_dynamo.data._ocr_routing_decision import _OCRRoutingDecision
from receipt_dynamo.data._places_cache import _PlacesCache
from receipt_dynamo.data._queue import _Queue
from receipt_dynamo.data._receipt import _Receipt
from receipt_dynamo.data._receipt_chatgpt_validation import (
    _ReceiptChatGPTValidation,
)
from receipt_dynamo.data._receipt_field import _ReceiptField
from receipt_dynamo.data._receipt_label_analysis import _ReceiptLabelAnalysis
from receipt_dynamo.data._receipt_letter import _ReceiptLetter
from receipt_dynamo.data._receipt_line import _ReceiptLine
from receipt_dynamo.data._receipt_line_item_analysis import (
    _ReceiptLineItemAnalysis,
)
from receipt_dynamo.data._receipt_metadata import _ReceiptMetadata
from receipt_dynamo.data._receipt_section import _ReceiptSection
from receipt_dynamo.data._receipt_structure_analysis import (
    _ReceiptStructureAnalysis,
)
from receipt_dynamo.data._receipt_validation_category import (
    _ReceiptValidationCategory,
)
from receipt_dynamo.data._receipt_validation_result import (
    _ReceiptValidationResult,
)
from receipt_dynamo.data._receipt_validation_summary import (
    _ReceiptValidationSummary,
)
from receipt_dynamo.data._receipt_word import _ReceiptWord
from receipt_dynamo.data._receipt_word_label import _ReceiptWordLabel
from receipt_dynamo.data._receipt_word_label_spatial_analysis import (
    _ReceiptWordLabelSpatialAnalysis,
)
from receipt_dynamo.data._word import _Word

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient


class DynamoClient(
    _Image,
    _Line,
    _Word,
    _Letter,
    _Receipt,
    _ReceiptLine,
    _ReceiptWord,
    _ReceiptLetter,
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
    _LabelCountCache,
    _ReceiptWordLabel,
    _ReceiptWordLabelSpatialAnalysis,
    _ReceiptField,
    _ReceiptLabelAnalysis,
    _ReceiptStructureAnalysis,
    _ReceiptLineItemAnalysis,
    _ReceiptValidationResult,
    _ReceiptValidationCategory,
    _ReceiptValidationSummary,
    _ReceiptChatGPTValidation,
    _BatchSummary,
    _EmbeddingBatchResult,
    _ReceiptMetadata,
    _CompletionBatchResult,
    _OCRJob,
    _ReceiptSection,
    _OCRRoutingDecision,
    _AIUsageMetric,
    _CompactionLock,
):
    """A class used to represent a DynamoDB client."""

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """Initializes a DynamoClient instance.

        Args:
            table_name (str): The name of the DynamoDB table.
            region (str, optional): The AWS region where the DynamoDB table is
                located. Defaults to "us-east-1".

        Attributes:
            _client (DynamoDBClient): The Boto3 DynamoDB client.
            table_name (str): The name of the DynamoDB table.
        """
        super().__init__()

        self._client: DynamoDBClient = boto3.client(
            "dynamodb", region_name=region
        )
        self.table_name = table_name
        # Ensure the table already exists
        try:
            self._client.describe_table(TableName=self.table_name)
        except self._client.exceptions.ResourceNotFoundException as e:
            raise ValueError(
                f"The table '{self.table_name}' does not exist in region "
                f"'{region}'."
            ) from e
