from dataclasses import dataclass
from typing import TYPE_CHECKING, Generator, List, Optional

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt_place import ReceiptPlace


@dataclass
class ReceiptDetails:
    """Container for a receipt and its related data."""

    receipt: Receipt
    lines: List[ReceiptLine]
    words: List[ReceiptWord]
    letters: List[ReceiptLetter]
    labels: List[ReceiptWordLabel]
    place: Optional["ReceiptPlace"] = None

    def __iter__(self) -> Generator[
        Receipt
        | List[ReceiptLine]
        | List[ReceiptWord]
        | List[ReceiptLetter]
        | List[ReceiptWordLabel]
        | Optional["ReceiptPlace"],
        None,
        None,
    ]:
        yield self.receipt
        yield self.lines
        yield self.words
        yield self.letters
        yield self.labels
        yield self.place
