import json
from receipt_label.utils import get_clients
import sys
from tqdm import tqdm

from receipt_dynamo.entities import ReceiptWordLabel, ReceiptWord, ReceiptLine

dynamo_client, openai_client, pinecone_index = get_clients()

READ_PINECONE = False
conflicted_vectors = []
if READ_PINECONE:
    for ids in pinecone_index.list(prefix="IMAGE", namespace="words"):
        response = pinecone_index.fetch(ids=ids, namespace="words")
        for vector_id, vector in response.vectors.items():
            metadata = vector.metadata
            valid = set(metadata.get("valid_labels", []))
            invalid = set(metadata.get("invalid_labels", []))
            conflicted = valid & invalid
            if conflicted:
                conflicted_vectors.append(
                    {
                        "id": vector_id,
                        "conflicted_labels": list(conflicted),
                        "valid_labels": list(valid),
                        "invalid_labels": list(invalid),
                    }
                )
    with open("conflicted_vectors.jsonl", "w") as f:
        for vector in conflicted_vectors:
            f.write(json.dumps(vector) + "\n")
else:
    with open("conflicted_vectors.jsonl", "r") as f:
        for line in f:
            conflicted_vectors.append(json.loads(line))


print(f"Found {len(conflicted_vectors)} conflicted vectors")

# Clean up each conflicted vector in Pinecone by updating only the invalid_labels metadata
for vec in tqdm(conflicted_vectors, desc="Updating Pinecone metadata"):
    valid = set(vec["valid_labels"])
    invalid = set(vec["invalid_labels"])
    cleaned_invalid = list(invalid - valid)

    pinecone_index.update(
        id=vec["id"],
        namespace="words",
        set_metadata={"invalid_labels": cleaned_invalid},
    )
