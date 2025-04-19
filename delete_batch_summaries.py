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

# Get all Places Cache Items
# print("Getting all Places Cache Items", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# places_cache_items, lek = dynamo_client.listPlacesCaches(
#     limit=25,
#     lastEvaluatedKey=None,
# )
# while lek is not None:
#     next_places_cache_items, lek = dynamo_client.listPlacesCaches(
#         limit=25,
#         lastEvaluatedKey=lek,
#     )
#     places_cache_items.extend(next_places_cache_items)
#     sys.stdout.write(".")
#     sys.stdout.flush()
# print()
# print(f"Found {len(places_cache_items)} Places Cache Items")

# # Delete all Places Cache Items
# print("Deleting all Places Cache Items", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# # Chunk places cache items into 25 and delete
# for i in range(0, len(places_cache_items), 25):
#     chunk = places_cache_items[i : i + 25]
#     dynamo_client.deletePlacesCaches(chunk)
#     sys.stdout.write(".")
#     sys.stdout.flush()

# List all Receipt Metadata
print("Listing all Receipt Metadata", end="")
sys.stdout.write(".")
sys.stdout.flush()
receipt_metadata, lek = dynamo_client.listReceiptMetadatas(
    limit=25,
    lastEvaluatedKey=None,
)
while lek is not None:
    next_receipt_metadata, lek = dynamo_client.listReceiptMetadatas(
        limit=25,
        lastEvaluatedKey=lek,
    )
    receipt_metadata.extend(next_receipt_metadata)
    sys.stdout.write(".")
    sys.stdout.flush()
print()
print(f"Found {len(receipt_metadata)} Receipt Metadata")

# Delete all Receipt Metadata
print("Deleting all Receipt Metadata", end="")
# chunk receipt metadata into 25 and delete
for i in range(0, len(receipt_metadata), 25):
    chunk = receipt_metadata[i : i + 25]
    dynamo_client.deleteReceiptMetadatas(chunk)
    sys.stdout.write(".")
    sys.stdout.flush()

print()
print("Done")
