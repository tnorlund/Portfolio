"""
DynamoDB utility package for receipt data.
"""

__version__ = "0.1.0"

# Entities must be imported first to avoid circular imports
from .entities.image import Image, itemToImage
from .entities.line import Line, itemToLine
from .entities.word import Word, itemToWord
from .entities.letter import Letter, itemToLetter
from .entities.receipt import Receipt, itemToReceipt
from .entities.receipt_line import ReceiptLine, itemToReceiptLine
from .entities.receipt_word import ReceiptWord, itemToReceiptWord
from .entities.receipt_letter import ReceiptLetter, itemToReceiptLetter
from .entities.receipt_window import ReceiptWindow, itemToReceiptWindow
from .entities.receipt_word_tag import ReceiptWordTag, itemToReceiptWordTag
from .entities.word_tag import WordTag, itemToWordTag
from .entities.gpt_validation import GPTValidation, itemToGPTValidation
from .entities.gpt_initial_tagging import GPTInitialTagging, itemToGPTInitialTagging
from .entities.job import Job, itemToJob
from .entities.job_status import JobStatus, itemToJobStatus
from .entities.job_resource import JobResource, itemToJobResource
from .entities.job_metric import JobMetric, itemToJobMetric
from .entities.job_checkpoint import JobCheckpoint, itemToJobCheckpoint
from .entities.job_log import JobLog, itemToJobLog

# Only after entities are imported, import data module classes
from .data.dynamo_client import DynamoClient
from .data.process import process
from .data.validate import validate
from .data.export_image import export_image
from .data.import_image import import_image
from .data.process_picture import process_picture

# For backward compatibility:
from .entities import *
from .data import *
