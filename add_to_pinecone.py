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
from receipt_label.submit_embedding_batch.submit_batch import (
    submit_openai_batch,
    create_batch_summary,
    list_receipt_word_labels,
    fetch_receipt_words,
    join_labels_with_words,
    chunk_joined_pairs,
    format_openai_input,
    write_ndjson,
    upload_ndjson_file,
)

env_vars = load_env("dev")
secrets = load_secrets("dev")
dynamodb_table = env_vars["dynamodb_table_name"]
pinecone_api_key = secrets["portfolio:PINECONE_API_KEY"]["value"]
openai_api_key = secrets["portfolio:OPENAI_API_KEY"]["value"]
pinecone_index = "receipt-validation-dev"
dynamo_client = DynamoClient(dynamodb_table)
batch_id = str(uuid4())


def embedForPinecone(
    receipt_word_label: ReceiptWordLabel, receipt_word: ReceiptWord
) -> str:
    centroid = receipt_word.calculate_centroid()
    x_center = centroid[0]
    y_center = centroid[1]
    return (
        f"{receipt_word.text} "
        f"[label={receipt_word_label.label}] "
        f"(pos={x_center:.4f},{y_center:.4f}) "
        f"angle={receipt_word.angle_degrees:.2f} "
        f"conf={receipt_word.confidence:.2f}"
    )


def list_receipt_word_labels() -> list:
    """Fetch ReceiptWordLabel items with validation_status == 'NONE'."""
    all_labels = []
    lek = None
    while True:
        labels, lek = dynamo_client.getReceiptWordLabelsByValidationStatus(
            validation_status="NONE",
            limit=25,
            lastEvaluatedKey=lek,
        )
        all_labels.extend(labels)
        if not lek:
            break
    return list(set(all_labels))


def fetch_receipt_words(labels: list) -> list:
    """Batch fetch ReceiptWord entities that match the given labels."""
    keys = [rwl.to_ReceiptWord_key() for rwl in labels]
    keys = [
        json.loads(j) for j in {json.dumps(d, sort_keys=True) for d in keys}
    ]
    results = []
    for i in range(0, len(keys), 25):
        chunk = keys[i : i + 25]
        results.extend(dynamo_client.getReceiptWordsByKeys(chunk))
    return results


def upload_ndjson_file(filepath: str):
    """Upload the NDJSON file to OpenAI."""
    return openai.files.create(file=open(filepath, "rb"), purpose="batch")


def submit_openai_batch(file_id: str):
    """Submit a batch embedding job to OpenAI using the uploaded file."""
    return openai.batches.create(
        input_file_id=file_id,
        endpoint="/v1/embeddings",
        parameters={"model": "text-embedding-3-small"},
    )


# Get all Receipt Word Labels with a Validation Status of "None"
print(
    "Getting all Receipt Word Labels with a Validation Status of NONE", end=""
)
sys.stdout.write(".")
sys.stdout.flush()
receipt_word_labels, lek = (
    dynamo_client.getReceiptWordLabelsByValidationStatus(
        validation_status="NONE",
        limit=25,
        lastEvaluatedKey=None,
    )
)
while lek is not None:
    next_receipt_word_labels, lek = (
        dynamo_client.getReceiptWordLabelsByValidationStatus(
            validation_status="NONE",
            limit=25,
            lastEvaluatedKey=lek,
        )
    )
    receipt_word_labels.extend(next_receipt_word_labels)
    sys.stdout.write(".")
    sys.stdout.flush()

print()
print(f"Found {len(receipt_word_labels)} Receipt Word Labels")

# Deduplicate by serializing to JSON, then back to dicts
receipt_word_labels = list(set(receipt_word_labels))
receipt_word_keys = [rwl.to_ReceiptWord_key() for rwl in receipt_word_labels]
receipt_word_keys = [
    json.loads(j)
    for j in {json.dumps(d, sort_keys=True) for d in receipt_word_keys}
]

# Get the corresponding Receipt words
receipt_words = []
print("Getting the corresponding Receipt words", end="")
sys.stdout.write(".")
sys.stdout.flush()
for i in range(0, len(receipt_word_keys), 25):
    chunk = receipt_word_keys[i : i + 25]
    receipt_words.extend(dynamo_client.getReceiptWordsByKeys(chunk))
    sys.stdout.write(".")
    sys.stdout.flush()

print()
print(f"Found {len(receipt_words)} Receipt Words")

# Map receipt words by composite ID
receipt_word_map = {
    (w.image_id, w.receipt_id, w.line_id, w.word_id): w for w in receipt_words
}
joined = []
for rwl in receipt_word_labels:
    key = (rwl.image_id, rwl.receipt_id, rwl.line_id, rwl.word_id)
    word = receipt_word_map.get(key)
    if word:
        joined.append((rwl, word))

# Parallelize the embedding in Production
joined = joined[0:500]

batch_summary = BatchSummary(
    batch_id=batch_id,
    batch_type="EMBEDDING",
    openai_batch_id="dummy",  # update when real embedding used
    submitted_at=datetime.now(timezone.utc),
    status="PENDING",  # update to "COMPLETED" later
    word_count=len(joined),
    result_file_id="N/A",  # you can store a reference to Pinecone if needed
    receipt_refs=[(rwl.image_id, rwl.receipt_id) for rwl, _ in joined],
)
dynamo_client.addBatchSummary(batch_summary)
retrieved_batch_summary = dynamo_client.getBatchSummary(batch_id)
print(retrieved_batch_summary)


print(f"Joined {len(joined)} Receipt Word Labels with Receipt Words")

text_to_embed = [embedForPinecone(rwl, word) for rwl, word in joined]
print(
    f"Embedding {len(text_to_embed)} Receipt Word Labels with Receipt Words:"
)
for text in text_to_embed[0:5]:
    print(text)

client = OpenAI(api_key=openai_api_key)

embeddings = [[0.0] * 1536 for _ in joined]
# TODO uncomment this
# embeddings = []
# for i in range(0, len(text_to_embed), 100):
#     batch = text_to_embed[i : i + 100]
#     response = client.embeddings.create(
#         model="text-embedding-3-small", input=batch
#     )
#     batch_embeddings = [d.embedding for d in response.data]
#     embeddings.extend(batch_embeddings)

results = []
for (rwl, word), emb in zip(joined, embeddings):
    pinecone_id = (
        f"RECEIPT#{rwl.receipt_id}#LINE#{rwl.line_id}#WORD#{rwl.word_id}"
    )
    result = EmbeddingBatchResult(
        batch_id=batch_id,
        image_id=rwl.image_id,
        receipt_id=rwl.receipt_id,
        line_id=rwl.line_id,
        word_id=rwl.word_id,
        pinecone_id=pinecone_id,
        text=word.text,
        label=rwl.label,
        status="SUCCESS",
        error_message=None,
    )
    results.append(result)

# TODO Remove this
dynamo_client.addEmbeddingBatchResults(results[0:5])
retrieved_embedding_batch_results, _ = (
    dynamo_client.listEmbeddingBatchResults()
)
print(retrieved_embedding_batch_results)

# ✅ GSI2/GSI3 validation
results_by_status, _ = dynamo_client.getEmbeddingBatchResultsByStatus(
    "SUCCESS"
)
print(f"Found {len(results_by_status)} results via GSI2")

if results_by_status:
    ref = results_by_status[0]
    results_by_receipt, _ = dynamo_client.getEmbeddingBatchResultsByReceipt(
        image_id=ref.image_id, receipt_id=ref.receipt_id
    )
    print(
        f"Found {len(results_by_receipt)} results via GSI3 for receipt {ref.receipt_id} on image {ref.image_id}"
    )

pinecone_vectors = []
for (rwl, word), emb in zip(joined, embeddings):
    pinecone_vectors.append(
        {
            "id": f"RECEIPT#{rwl.receipt_id}#LINE#{rwl.line_id}#WORD#{rwl.word_id}",
            "values": emb,
            "metadata": {
                "image_id": rwl.image_id,
                "receipt_id": rwl.receipt_id,
                "line_id": rwl.line_id,
                "word_id": rwl.word_id,
                "text": word.text,
                "label": rwl.label,
                "angle_degrees": word.angle_degrees,
                "confidence": word.confidence,
            },
        }
    )

# Create a Pinecone index if it doesn't exist
pc = Pinecone(api_key=pinecone_api_key)
index = pc.Index(pinecone_index)

# Update the batch summary to COMPLETED
batch_summary.status = "COMPLETED"
dynamo_client.updateBatchSummary(batch_summary)


# Clean up
dynamo_client.deleteBatchSummary(retrieved_batch_summary)
dynamo_client.deleteEmbeddingBatchResults(retrieved_embedding_batch_results)
