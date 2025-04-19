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

# Get all batch summaries
print("Getting all Batch Summaries", end="")
sys.stdout.write(".")
sys.stdout.flush()
batch_summaries, lek = dynamo_client.listBatchSummaries(
    limit=25,
    lastEvaluatedKey=None,
)
while lek is not None:
    batch_summaries, lek = dynamo_client.listBatchSummaries(
        limit=25,
        lastEvaluatedKey=lek,
    )
    sys.stdout.write(".")
    sys.stdout.flush()
print()
print(f"Found {len(batch_summaries)} Batch Summaries")

# Delete all Batch Summaries
print("Deleting all Batch Summaries", end="")
sys.stdout.write(".")
sys.stdout.flush()
# Chunk batch summaries into 25 and delete
for i in range(0, len(batch_summaries), 25):
    chunk = batch_summaries[i : i + 25]
    dynamo_client.deleteBatchSummaries(chunk)
    sys.stdout.write(".")
    sys.stdout.flush()
print()

# Get all Embeeding Batch Results
print("Getting all Embeeding Batch Results", end="")
sys.stdout.write(".")
sys.stdout.flush()
embedding_batch_results, lek = dynamo_client.listEmbeddingBatchResults(
    limit=25,
    lastEvaluatedKey=None,
)
dynamo_client.deleteEmbeddingBatchResults(embedding_batch_results)
while lek is not None:
    embedding_batch_results, lek = dynamo_client.listEmbeddingBatchResults(
        limit=25,
        lastEvaluatedKey=lek,
    )
    dynamo_client.deleteEmbeddingBatchResults(embedding_batch_results)
    sys.stdout.write(".")
    sys.stdout.flush()
print()
print(f"Found {len(embedding_batch_results)} Embedding Batch Results")


# List all Receipt Words Labels
print("Listing all Receipt Words Labels", end="")
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

# Set All Receipt Word Labels to have a validation Status of "NONE"
for rwl in receipt_word_labels:
    rwl.validation_status = "NONE"

# Update them to have a validation Status of "NONE" in chunks of 25
print("Updating Receipt Word Labels", end="")
sys.stdout.write(".")
sys.stdout.flush()
for i in range(0, len(receipt_word_labels), 25):
    chunk = receipt_word_labels[i : i + 25]
    dynamo_client.updateReceiptWordLabels(chunk)
    sys.stdout.write(".")
    sys.stdout.flush()
print()
print("Done")


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
# print("Listing all Receipt Metadata", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# receipt_metadata, lek = dynamo_client.listReceiptMetadatas(
#     limit=25,
#     lastEvaluatedKey=None,
# )
# while lek is not None:
#     next_receipt_metadata, lek = dynamo_client.listReceiptMetadatas(
#         limit=25,
#         lastEvaluatedKey=lek,
#     )
#     receipt_metadata.extend(next_receipt_metadata)
#     sys.stdout.write(".")
#     sys.stdout.flush()
# print()
# print(f"Found {len(receipt_metadata)} Receipt Metadata")

# # Delete all Receipt Metadata
# print("Deleting all Receipt Metadata", end="")
# # chunk receipt metadata into 25 and delete
# for i in range(0, len(receipt_metadata), 25):
#     chunk = receipt_metadata[i : i + 25]
#     dynamo_client.deleteReceiptMetadatas(chunk)
#     sys.stdout.write(".")
#     sys.stdout.flush()

# print()
# print("Done")
