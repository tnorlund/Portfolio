import json
from receipt_label.utils import get_clients
import sys
from receipt_dynamo.entities import ReceiptWordLabel, ReceiptWord, ReceiptLine

dynamo_client, openai_client, pinecone_index = get_clients()


def get_word(label: ReceiptWordLabel, words: list[ReceiptWord]) -> ReceiptWord:
    return next(
        (
            word
            for word in words
            if word.image_id == label.image_id
            and word.receipt_id == label.receipt_id
            and word.line_id == label.line_id
            and word.word_id == label.word_id
        ),
        None,
    )


with open("words.ndjson", "r") as f:
    words = [ReceiptWord(**json.loads(line)) for line in f]

with open("receipt_word_labels.ndjson", "r") as f:
    receipt_word_labels = [ReceiptWordLabel(**json.loads(line)) for line in f]

with open("receipt_lines.ndjson", "r") as f:
    receipt_lines = [ReceiptLine(**json.loads(line)) for line in f]

# Get the word for each label
label_type = "ADDRESS_LINE"
labels_to_review = [
    label
    for label in receipt_word_labels
    if label.label == label_type and label.validation_status == "NEEDS_REVIEW"
]

# Get unique combinations of Receipt ID and Image ID
receipt_image_ids = set(
    (label.receipt_id, label.image_id) for label in labels_to_review
)

# For each unique combination, get the metadata
metadata = {}
for label in labels_to_review:
    metadata[(label.receipt_id, label.image_id)] = dynamo_client.getReceiptMetadata(
        receipt_id=label.receipt_id,
        image_id=label.image_id,
    )

# Get the receipt lines for each unique combination
lines = {}
for label in labels_to_review:
    lines[(label.receipt_id, label.image_id)] = [
        line
        for line in receipt_lines
        if line.receipt_id == label.receipt_id and line.image_id == label.image_id
    ]

for label_to_review in labels_to_review:
    other_labels = [
        label
        for label in receipt_word_labels
        if label.receipt_id == label_to_review.receipt_id
        and label.image_id == label_to_review.image_id
        and label.line_id == label_to_review.line_id
        and label.word_id == label_to_review.word_id
    ]
    word = get_word(label_to_review, words)
    invalid_labels = [
        label.label for label in other_labels if label.validation_status == "INVALID"
    ]
    pending_labels = [
        label.label for label in other_labels if label.validation_status == "PENDING"
    ]
    valid_labels = [
        label.label for label in other_labels if label.validation_status == "VALID"
    ]
    none_labels = [
        label.label for label in other_labels if label.validation_status == "NONE"
    ]
    needs_review_labels = [
        label.label
        for label in other_labels
        if label.validation_status == "NEEDS_REVIEW"
    ]
    pinecone_id = f"IMAGE#{label_to_review.image_id}#RECEIPT#{label_to_review.receipt_id:05d}#LINE#{label_to_review.line_id:05d}#WORD#{label_to_review.word_id:05d}"
    vector_response = pinecone_index.fetch(ids=[pinecone_id], namespace="words")
    vector = (
        vector_response.vectors[pinecone_id].values
        if pinecone_id in vector_response.vectors
        else None
    )

    if vector:
        metadata_filter = {"valid_labels": {"$in": [label.label]}}
        query_response = pinecone_index.query(
            vector=vector,
            top_k=5,
            include_metadata=True,
            namespace="words",
            filter=metadata_filter,
        )
        print("We're trying to validate:", word.text)
        print("Invalid labels:", invalid_labels)
        print("Pending labels:", pending_labels)
        print("Valid labels:", valid_labels)
        print("None labels:", none_labels)
        print("Needs review labels:", needs_review_labels)
        for match in query_response.matches:
            print(
                f"  - Score: {match.score:.3f}, ID: {match.id}, Metadata: {match.metadata}"
            )
    else:
        print(f"Vector not found for {pinecone_id}")
    break

# Make a new NDJSON file with the labels and their words
# with open(f"{label_type}_labels_and_words.ndjson", "w") as f:
#     for label in labels:
#         word = get_word(label, words)
#         _out = {
#             "label": label.label,
#             "text": word.text,
#         }
#         f.write(json.dumps(dict(label)) + "\n")
#         f.write(json.dumps(dict(word)) + "\n")
