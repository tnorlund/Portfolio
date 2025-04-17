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

# Get all Embedding Batch Results
# print("Getting all Embedding Batch Results", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# embedding_batch_results, lek = dynamo_client.listEmbeddingBatchResults(
#     limit=25,
#     lastEvaluatedKey=None,
# )
# while lek is not None:
#     next_embedding_batch_results, lek = (
#         dynamo_client.listEmbeddingBatchResults(
#             limit=25,
#             lastEvaluatedKey=lek,
#         )
#     )
#     embedding_batch_results.extend(next_embedding_batch_results)
#     sys.stdout.write(".")
#     sys.stdout.flush()

# print()
# print(f"Found {len(embedding_batch_results)} Embedding Batch Results")

# # Delete all Embedding Batch Results
# print("Deleting all Embedding Batch Results", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# # Chunk embedding_batch_results into 25 and delete
# for i in range(0, len(embedding_batch_results), 25):
#     chunk = embedding_batch_results[i : i + 25]
#     for er in chunk:
#         dynamo_client.deleteEmbeddingBatchResults(chunk)
#     sys.stdout.write(".")
#     sys.stdout.flush()

# print()
# print("Done")

# Get all Batch Summaries
print("Getting all Batch Summaries", end="")
sys.stdout.write(".")
sys.stdout.flush()
batch_summaries, lek = dynamo_client.listBatchSummaries(
    limit=25,
    lastEvaluatedKey=None,
)
while lek is not None:
    next_batch_summaries, lek = dynamo_client.listBatchSummaries(
        limit=25,
        lastEvaluatedKey=lek,
    )
    batch_summaries.extend(next_batch_summaries)
    sys.stdout.write(".")
    sys.stdout.flush()

# print()
# print(f"Found {len(batch_summaries)} Batch Summaries")

# # for bs in batch_summaries:
# #     print(bs.to_item())

# Update all Batch Summaries to have status "PENDING"
for bs in batch_summaries:
    bs.status = "PENDING"
dynamo_client.updateBatchSummaries(batch_summaries)

# # Delete all Batch Summaries
# print("Deleting all Batch Summaries", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# for i in range(0, len(batch_summaries), 25):
#     chunk = batch_summaries[i : i + 25]
#     for bs in chunk:
#         dynamo_client.deleteBatchSummaries(chunk)
#     sys.stdout.write(".")
#     sys.stdout.flush()

# print()

# Get ALl receipt word labels
# print("Getting all receipt word labels", end="")
# sys.stdout.write(".")
# sys.stdout.flush()
# receipt_word_labels, lek = dynamo_client.listReceiptWordLabels(
#     limit=25,
#     lastEvaluatedKey=None,
# )
# while lek is not None:
#     next_receipt_word_labels, lek = dynamo_client.listReceiptWordLabels(
#         limit=25,
#         lastEvaluatedKey=lek,
#     )
#     receipt_word_labels.extend(next_receipt_word_labels)
#     sys.stdout.write(".")
#     sys.stdout.flush()

# print()
# print(f"Found {len(receipt_word_labels)} receipt word labels")

# # Update all receipt word labels to have Validation Status "NONE"
# print(
#     "Updating all receipt word labels to have Validation Status 'NONE'", end=""
# )
# sys.stdout.write(".")
# sys.stdout.flush()
# for rwl in receipt_word_labels:
#     rwl.validation_status = "NONE"

# # Chunk receipt word labels into 25 and update
# for i in range(0, len(receipt_word_labels), 25):
#     chunk = receipt_word_labels[i : i + 25]
#     dynamo_client.updateReceiptWordLabels(chunk)
#     sys.stdout.write(".")
#     sys.stdout.flush()
