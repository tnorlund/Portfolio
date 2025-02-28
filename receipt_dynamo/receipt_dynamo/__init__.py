"""
DynamoDB utility package for receipt data.
"""

__version__ = "0.1.0"

# Define public API - this tells both users and linters what's intentionally exported
__all__ = [# Entity classes
    "Image", "itemToImage",
    "Letter", "itemToLetter",
    "Line", "itemToLine",
    "Receipt", "itemToReceipt",
    "ReceiptLetter", "itemToReceiptLetter",
    "ReceiptLine", "itemToReceiptLine",
    "ReceiptWord", "itemToReceiptWord",
    "ReceiptWordTag", "itemToReceiptWordTag",
    "Word", "itemToWord",
    "WordTag", "itemToWordTag",
    "GPTValidation", "itemToGPTValidation",
    "GPTInitialTagging", "itemToGPTInitialTagging",
    "Job", "itemToJob",
    "JobMetric", "itemToJobMetric",
    "JobResource", "itemToJobResource",
    "JobStatus", "itemToJobStatus",
    "ReceiptWindow", "itemToReceiptWindow",
    "Instance", "itemToInstance",
    "InstanceJob", "itemToInstanceJob",
    # Data operations
    "DynamoClient",
    "process",
    "export_image",
    "import_image",]

# Entities must be imported first to avoid circular imports
from receipt_dynamo.entities.image import Image, itemToImage
from receipt_dynamo.entities.letter import Letter, itemToLetter
from receipt_dynamo.entities.line import Line, itemToLine
from receipt_dynamo.entities.receipt import Receipt, itemToReceipt
from receipt_dynamo.entities.receipt_letter import (ReceiptLetter,
    itemToReceiptLetter,)
from receipt_dynamo.entities.receipt_line import ReceiptLine, itemToReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord, itemToReceiptWord
from receipt_dynamo.entities.receipt_word_tag import (ReceiptWordTag,
    itemToReceiptWordTag,)
from receipt_dynamo.entities.word import Word, itemToWord
from receipt_dynamo.entities.word_tag import WordTag, itemToWordTag
from receipt_dynamo.entities.gpt_validation import (GPTValidation,
    itemToGPTValidation,)
from receipt_dynamo.entities.gpt_initial_tagging import (GPTInitialTagging,
    itemToGPTInitialTagging,)
from receipt_dynamo.entities.job import Job, itemToJob
from receipt_dynamo.entities.job_metric import JobMetric, itemToJobMetric
from receipt_dynamo.entities.job_resource import JobResource, itemToJobResource
from receipt_dynamo.entities.job_status import JobStatus, itemToJobStatus
from receipt_dynamo.entities.receipt_window import (ReceiptWindow,
    itemToReceiptWindow,)
from receipt_dynamo.entities.instance import Instance, itemToInstance
from receipt_dynamo.entities.instance_job import InstanceJob, itemToInstanceJob

# Only import what's actually used elsewhere in the package
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.process import process
from receipt_dynamo.data.export_image import export_image
from receipt_dynamo.data.import_image import import_image

# For backward compatibility:
