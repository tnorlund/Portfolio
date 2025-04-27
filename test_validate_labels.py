import random
from receipt_label.utils.clients import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()

CORE_LABELS = [
    # Merchant & store info
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    # Location/address (either as one line or broken out)
    "ADDRESS_LINE",  # or, for finer breakdown:
    # "ADDRESS_NUMBER",
    # "STREET_NAME",
    # "CITY",
    # "STATE",
    # "POSTAL_CODE",
    # Transaction info
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",  # if you want to distinguish coupons vs. generic discounts
    # Line‑item fields
    "PRODUCT_NAME",  # or ITEM_NAME
    "QUANTITY",  # or ITEM_QUANTITY
    "UNIT_PRICE",  # or ITEM_PRICE
    "LINE_TOTAL",  # or ITEM_TOTAL
    # Totals & taxes
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",  # or TOTAL
]


def generate_pinecone_id(image_id, receipt_id, line_id, word_id):
    # IMAGE#1bfb07b7-aefd-4579-b721-df597471b96b#RECEIPT#00001#LINE#00002#WORD#00003
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


label_text = "ITEM_NAME"
labels, _ = dynamo_client.getReceiptWordLabelsByLabel(label=label_text)

# receipts, _ = dynamo_client.listReceipts()
# print(f"Found {len(receipts)} receipts")

# # Randomly select a receipt
# # receipt = random.choice(receipts)
# receipt_id = 1
# image_id = "75ec2ac9-e043-4270-b7ec-67e4f225dae9"
# print(f"Selected receipt {image_id}-{receipt_id}")

# # Get the words for the receipt
# (receipt, lines, words, letters, tags, labels) = dynamo_client.getReceiptDetails(
#     image_id=image_id, receipt_id=receipt_id
# )
# metadata = dynamo_client.getReceiptMetadata(image_id=image_id, receipt_id=receipt_id)

# print(f"Found {len(words)} words")
# # Build lookup dictionaries
# word_text_by_id = {w.word_id: w.text for w in words}
# labels_by_word = {}
# for lbl in labels:
#     # Use composite key of (line_id, word_id) to group labels per word
#     key = (lbl.line_id, lbl.word_id)
#     labels_by_word.setdefault(key, []).append(lbl.label)


# # Build a structured list of each word and its labels
# word_label_mappings = []
# for word in words:
#     key = (word.line_id, word.word_id)
#     if "ITEM_NAME" in labels_by_word.get(key, []):
#         word_label_mappings.append(
#             {
#                 "text": word.text,
#                 "line_id": word.line_id,
#                 "word_id": word.word_id,
#                 "labels": labels_by_word.get(key, []),
#             }
#         )


# # Output the final structure
# print("Word-Label mappings:")
# for mapping in word_label_mappings:
for label in labels:
    pinecone_id = generate_pinecone_id(
        label.image_id, label.receipt_id, label.line_id, label.word_id
    )
    response = pinecone_index.fetch(ids=[pinecone_id], namespace="words")
    vector_entry = response.vectors.get(pinecone_id)
    if vector_entry is None:
        raise KeyError(f"No vector found for ID {pinecone_id}")

    embedding_vector = vector_entry.values  # list[float]
    stored_metadata = getattr(vector_entry, "metadata", {}) or {}
    # inside your loop over word_label_mappings, once you have embedding_vector:
    top_k = 5
    neighbors = pinecone_index.query(
        vector=embedding_vector,
        top_k=top_k,
        include_metadata=True,  # so you get back the stored text/label
        namespace="words",  # whatever namespace you upserted into
        # optional filter to narrow by merchant or any other metadata:
        # filter={"merchant_name": {"$eq": merchant_name}}
    ).matches

    print(f"Top {top_k} neighbors for “{label_text}”:\n")
    for m in neighbors:
        print(f"  ID:    {m.id}")
        print(f"  Score: {m.score:.4f}")
        print(f"  Text:  {m.metadata.get('text')}")
        print(f"  Label: {m.metadata.get('label')}\n")
