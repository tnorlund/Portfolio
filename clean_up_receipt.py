import os
import json
from collections import Counter
from pathlib import Path
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordLabel,
    ReceiptMetadata,
)


with open(
    Path("dev.local_export") / "82c4720e-1c52-4dad-b8af-8605f84dc2ab.json",
    "r",
    encoding="utf-8",
) as f:
    data = json.load(f)

receipts = [Receipt(**r) for r in data["receipts"]]
receipt_lines = [ReceiptLine(**l) for l in data["receipt_lines"]]
receipt_words = [ReceiptWord(**w) for w in data["receipt_words"]]
receipt_letters = [ReceiptLetter(**l) for l in data["receipt_letters"]]

# Get the count per receipt

receipt_ids = [receipt.receipt_id for receipt in receipts]

receipt_ids_to_delete = []
receipts_to_delete: list[Receipt] = []
for receipt_id in receipt_ids:
    receipt_lines_for_receipt = [
        line for line in receipt_lines if line.receipt_id == receipt_id
    ]
    receipt_words_for_receipt = [
        word for word in receipt_words if word.receipt_id == receipt_id
    ]
    receipt_letters_for_receipt = [
        letter for letter in receipt_letters if letter.receipt_id == receipt_id
    ]

    print(
        f"Receipt {receipt_id} has {len(receipt_lines_for_receipt)} lines, {len(receipt_words_for_receipt)} words, and {len(receipt_letters_for_receipt)} letters"
    )

    # Get the count per letter
    letter_counts = Counter(
        letter.text for letter in receipt_letters_for_receipt
    )
    print(f"Letter counts: {letter_counts}")

    # Get the count per word
    word_counts = Counter(word.text for word in receipt_words_for_receipt)
    print(f"Word counts: {word_counts}")

    # Get the count per line
    line_counts = Counter(line.text for line in receipt_lines_for_receipt)
    print(f"Line counts: {line_counts}")

    if len(receipt_lines_for_receipt) == 0:
        receipt = receipts[receipt_id - 1]
        receipts_to_delete.append(receipt)

print(f"Receipts to delete: {receipts_to_delete}")

# Delete the receipts
for receipt in receipts_to_delete:
    print(f"Deleting receipt {receipt.receipt_id}")
    client = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
    client.delete_receipt(receipt)
