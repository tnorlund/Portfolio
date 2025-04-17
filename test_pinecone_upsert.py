import json
from receipt_label.poll_embedding_batch import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
)
from receipt_label.utils import get_clients


dynamo_client, openai_client, pinecone_index = get_clients()


def get_embedding(input_text):
    response = openai_client.embeddings.create(
        input=input_text, model="text-embedding-3-small"
    )
    return response.data[0].embedding


# List 25 receipt word labels
labels, lek = dynamo_client.listReceiptWordLabels(
    limit=25,
    lastEvaluatedKey=None,
)

# Fetch receipt words for each label
receipt_words = []
keys = [label.to_ReceiptWord_key() for label in labels]
keys = list({json.dumps(k, sort_keys=True): k for k in keys}.values())
for i in range(0, len(keys), 25):
    chunk = keys[i : i + 25]
    receipt_words.extend(dynamo_client.getReceiptWordsByKeys(chunk))

# Form the pinecone vectors
# "{word.text} [label={label}] (pos={x:.4f},{y:.4f}) angle={angle} conf={conf}"
vectors = []
for label, word in zip(labels, receipt_words):
    centroid = word.calculate_centroid()
    input_text = (
        f"{word.text} [label={label.label}] "
        f"(pos={centroid[0]:.4f},{centroid[1]:.4f}) "
        f"angle={word.angle_degrees:.2f} "
        f"conf={word.confidence:.2f}"
    )
    embedding = get_embedding(input_text)
    query_result = pinecone_index.query(
        vector=embedding, top_k=5, include_metadata=True
    )
    print(f"\nWord: {word.text}  Label: {label.label}")
    for i, match in enumerate(query_result.matches):
        print(
            f"{i+1}. {match.metadata.get('text')} (label: {match.metadata.get('label')}) — score: {match.score:.4f}"
        )
