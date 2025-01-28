# test__gpt.py

import pytest
from typing import Literal, Tuple
from dynamo import (
    DynamoClient,
    Image,
    Line,
    Word,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)
from ._fixtures import sample_gpt_receipt


def test_gpt_receipt(
    sample_gpt_receipt: Tuple[
        list[Image],
        list[Line],
        list[Word],
        list[Letter],
        list[Receipt],
        list[ReceiptLine],
        list[ReceiptWord],
        list[ReceiptLetter],
    ],
    dynamodb_table: Literal["MyMockedTable"],
):
    # Arrange
    client = DynamoClient("MyMockedTable")
    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
    ) = sample_gpt_receipt
    client.addImages(images)
    client.addLines(lines)
    client.addWords(words)
    client.addLetters(letters)
    client.addReceipts(receipts)
    client.addReceiptLines(receipt_lines)
    client.addReceiptWords(receipt_words)
    client.addReceiptLetters(receipt_letters)
    
