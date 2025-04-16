import os
import subprocess
import json
from uuid import uuid4
from datetime import datetime, timezone
import sys
from pinecone import Pinecone
from openai import OpenAI

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    ReceiptWordLabel,
    ReceiptWord,
    BatchSummary,
    EmbeddingBatchResult,
)
from receipt_dynamo.data._pulumi import load_env, load_secrets

env_vars = load_env("dev")
secrets = load_secrets("dev")
dynamodb_table = env_vars["dynamodb_table_name"]
# pinecone_api_key = secrets["portfolio:PINECONE_API_KEY"]["value"]
# openai_api_key = secrets["portfolio:OPENAI_API_KEY"]["value"]
# pinecone_index = "receipt-validation-dev"
dynamo_client = DynamoClient(dynamodb_table)
batch_id = str(uuid4())


# Get all Receipt Word Labels
print("Getting all Receipt Word Labels", end="")
sys.stdout.write(".")
sys.stdout.flush()
receipt_word_labels, lek = dynamo_client.listReceiptWordLabels(
    limit=25,
    lastEvaluatedKey=None,
)
while lek is not None:
    next_receipt_word_labels, lek = dynamo_client.listReceiptWordLabels(
        limit=25,
        lastEvaluatedKey=lek,
    )
    receipt_word_labels.extend(next_receipt_word_labels)
    sys.stdout.write(".")
    sys.stdout.flush()

print()
print(f"Found {len(receipt_word_labels)} Receipt Word Labels")


for rwl in receipt_word_labels:
    rwl.label_validation_status = "NONE"

print("Updating Receipt Word Labels", end="")
sys.stdout.write(".")
sys.stdout.flush()
for i in range(0, len(receipt_word_labels), 25):
    chunk = receipt_word_labels[i : i + 25]
    dynamo_client.updateReceiptWordLabels(chunk)
    sys.stdout.write(".")
    sys.stdout.flush()

print()

print("Updated Receipt Word Labels")

# print("Checking uniqueness of ReceiptWordLabel keys...")
# key_set = set()
# duplicates = []
# for rwl in receipt_word_labels:
#     key = (rwl.image_id, rwl.receipt_id, rwl.line_id, rwl.word_id, rwl.label)
#     if key in key_set:
#         duplicates.append(key)
#     else:
#         key_set.add(key)

# if duplicates:
#     print(f"Found {len(duplicates)} duplicate keys:")
#     for dup in duplicates:
#         print("  Duplicate:", dup)
# else:
#     print("✅ All ReceiptWordLabels have unique key combinations.")
