from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt_place import ReceiptPlace


@dataclass
class ReceiptDetails:
    """Container for a receipt and its related data.

    Note: The optimized GSI4 query (get_receipt_details) does not fetch letters
    by design, as they are rarely needed. The letters field defaults to an
    empty list for backward compatibility.
    """

    receipt: Receipt
    lines: list[ReceiptLine]
    words: list[ReceiptWord]
    labels: list[ReceiptWordLabel]
    letters: list[ReceiptLetter] = field(default_factory=list)
    place: "ReceiptPlace" | None = None

    def __iter__(self) -> Generator[
        Receipt
        | list[ReceiptLine]
        | list[ReceiptWord]
        | list[ReceiptLetter]
        | list[ReceiptWordLabel]
        | "ReceiptPlace" | None,
        None,
        None,
    ]:
        yield self.receipt
        yield self.lines
        yield self.words
        yield self.letters
        yield self.labels
        yield self.place
