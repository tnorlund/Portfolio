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
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.word import Word


@dataclass
class ImageDetails:
    """Collection of all data associated with an image."""

    images: list[Image]
    lines: list[Line]
    words: list[Word]
    letters: list[Letter]
    receipts: list[Receipt]
    receipt_lines: list[ReceiptLine]
    receipt_words: list[ReceiptWord]
    receipt_letters: list[ReceiptLetter]
    receipt_word_labels: list[ReceiptWordLabel]
    receipt_places: list[ReceiptPlace]
    ocr_jobs: list[OCRJob]
    ocr_routing_decisions: list[OCRRoutingDecision]

    def __iter__(self) -> Generator[List, None, None]:
        yield self.images
        yield self.lines
        yield self.words
        yield self.letters
        yield self.receipts
        yield self.receipt_lines
        yield self.receipt_words
        yield self.receipt_letters
        yield self.receipt_word_labels
        yield self.receipt_places
        yield self.ocr_jobs
        yield self.ocr_routing_decisions
