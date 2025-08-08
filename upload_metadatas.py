import os
import json
from pathlib import Path
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptMetadata,
)

#  Read the legitimate_metadata.json file
with open("three_missing_metadata.json", "r") as f:
    metadata_dict = json.load(f)

metadatas = [
    ReceiptMetadata(
        **{**m, "timestamp": datetime.fromisoformat(m["timestamp"])}
    )
    for m in metadata_dict["metadata"]
]

#  Upload the metadata to the DynamoDB table
client = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
client.add_receipt_metadatas(metadatas)
