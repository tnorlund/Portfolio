import json
from receipt_dynamo.entities import ReceiptWord
from receipt_label.submit_embedding_batch.submit_batch import (
    get_hybrid_context,
    format_word_context_embedding,
    get_word_position,
)
from receipt_label.utils import get_clients
from collections import Counter
import logging

image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
receipt_id = 1

dynamo_client, openai_client, pinecone_index = get_clients()

# (
#     receipt,
#     lines,
#     words,
#     letters,
#     tags,
#     labels,
# ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
# nd_json = "\n".join([json.dumps(dict(word)) for word in words])
# with open("test_list_lines.ndjson", "w") as f:
#     f.write(nd_json)

# deserialized = []
# with open("test_list_lines.ndjson", "r") as f:
#     for line in f:
#         word = json.loads(line)
#         deserialized.append(ReceiptWord(**word))

# print(f"matched? {words == deserialized}")

receipts, _ = dynamo_client.listReceipts(limit=5)


# all_words = []
for receipt in receipts:
    print(f"Receipt: {receipt.image_id} {receipt.receipt_id}")
    (
        receipt,
        lines,
        words,
        letters,
        tags,
        labels,
    ) = dynamo_client.getReceiptDetails(receipt.image_id, receipt.receipt_id)

    for word in words[0:5]:
        print(word)

#     all_words.extend(words)

# # Make a dictionary of words by image_id and receipt_id
# words_by_image = {}
# for word in all_words:
#     # Get or create the sub-dictionary for this image_id
#     image_dict = words_by_image.setdefault(word.image_id, {})
#     # Get or create the list for this receipt_id within that image
#     receipt_list = image_dict.setdefault(word.receipt_id, [])
#     # Append the word to the list
#     receipt_list.append(word)

# print(words_by_image.keys())
# print(words_by_image[list(words_by_image.keys())[0]].keys())


# #     # Get all words in the first 5 lines
# #     for line in lines[0:5]:
# #         for word in words:
# #             if word.line_id == line.line_id:
# #                 print(format_word_context_embedding(word, words))
