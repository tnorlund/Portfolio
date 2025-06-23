from dataclasses import dataclass
from typing import Generator, List

from receipt_dynamo.entities.image import Image
from receipt_dynamo.entities.letter import Letter
from receipt_dynamo.entities.line import Line
from receipt_dynamo.entities.ocr_job import OCRJob
from receipt_dynamo.entities.ocr_routing_decision import OCRRoutingDecision
from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_tag import ReceiptWordTag
from receipt_dynamo.entities.word import Word
from receipt_dynamo.entities.word_tag import WordTag


@dataclass
class ImageDetails:
    """Collection of all data associated with an image."""

    images: List[Image]
    lines: List[Line]
    words: List[Word]
    word_tags: List[WordTag]
    letters: List[Letter]
    receipts: List[Receipt]
    receipt_lines: List[ReceiptLine]
    receipt_words: List[ReceiptWord]
    receipt_word_tags: List[ReceiptWordTag]
    receipt_letters: List[ReceiptLetter]
    ocr_jobs: List[OCRJob]
    ocr_routing_decisions: List[OCRRoutingDecision]
    receipt_metadatas: List[ReceiptMetadata]

    def __iter__(self) -> Generator[List, None, None]:
        yield self.images
        yield self.lines
        yield self.words
        yield self.word_tags
        yield self.letters
        yield self.receipts
        yield self.receipt_lines
        yield self.receipt_words
        yield self.receipt_word_tags
        yield self.receipt_letters
        yield self.ocr_jobs
        yield self.ocr_routing_decisions
        yield self.receipt_metadatas
