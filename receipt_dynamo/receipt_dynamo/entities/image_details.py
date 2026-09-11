from copy import copy
from dataclasses import dataclass, field, fields
from typing import Any, Generator

from receipt_dynamo.entities.image import Image
from receipt_dynamo.entities.letter import Letter
from receipt_dynamo.entities.line import Line
from receipt_dynamo.entities.ocr_job import OCRJob
from receipt_dynamo.entities.ocr_routing_decision import OCRRoutingDecision
from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_barcode import ReceiptBarcode
from receipt_dynamo.entities.receipt_embedding import ReceiptEmbedding
from receipt_dynamo.entities.receipt_fact_override import ReceiptFactOverride
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_row import ReceiptRow
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo.entities.receipt_summary import ReceiptSummary
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.word import Word


@dataclass
class ImageDetails:
    """Collection of all data associated with an image."""

    images: list[Image] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    letters: list[Letter] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)
    receipt_lines: list[ReceiptLine] = field(default_factory=list)
    receipt_words: list[ReceiptWord] = field(default_factory=list)
    receipt_letters: list[ReceiptLetter] = field(default_factory=list)
    receipt_word_labels: list[ReceiptWordLabel] = field(default_factory=list)
    receipt_places: list[ReceiptPlace] = field(default_factory=list)
    receipt_barcodes: list[ReceiptBarcode] = field(default_factory=list)
    # Derived rows (rows -> sections -> line items) and the owner-stated
    # fact override. These are produced by their own pipelines rather than
    # by OCR, but nothing regenerates them in a destination environment
    # after a cross-environment copy, so they must travel with the image.
    receipt_rows: list[ReceiptRow] = field(default_factory=list)
    receipt_sections: list[ReceiptSection] = field(default_factory=list)
    receipt_line_items: list[ReceiptLineItem] = field(default_factory=list)
    receipt_summaries: list[ReceiptSummary] = field(default_factory=list)
    receipt_fact_overrides: list[ReceiptFactOverride] = field(
        default_factory=list
    )
    receipt_embeddings: list[ReceiptEmbedding] = field(default_factory=list)
    ocr_jobs: list[OCRJob] = field(default_factory=list)
    ocr_routing_decisions: list[OCRRoutingDecision] = field(
        default_factory=list
    )

    def __post_init__(self) -> None:
        """Reject non-list collections and detach caller-owned containers."""
        for field_info in fields(self):
            value = getattr(self, field_info.name)
            if not isinstance(value, list):
                raise ValueError(f"{field_info.name} must be a list")
            setattr(self, field_info.name, copy(value))

    def __iter__(self) -> Generator[list[Any], None, None]:
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
        yield self.receipt_barcodes
        yield self.receipt_rows
        yield self.receipt_sections
        yield self.receipt_line_items
        yield self.receipt_summaries
        yield self.receipt_fact_overrides
        yield self.receipt_embeddings
        yield self.ocr_jobs
        yield self.ocr_routing_decisions
