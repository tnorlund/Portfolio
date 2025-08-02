from dataclasses import dataclass
from typing import Generator, List

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


@dataclass
class ReceiptDetails:
    """Container for a receipt and its related data."""

    receipt: Receipt
    lines: List[ReceiptLine]
    words: List[ReceiptWord]
    letters: List[ReceiptLetter]
    labels: List[ReceiptWordLabel]

    def __iter__(self) -> Generator[List | Receipt, None, None]:
        yield self.receipt
        yield self.lines
        yield self.words
        yield self.letters
        yield self.labels
