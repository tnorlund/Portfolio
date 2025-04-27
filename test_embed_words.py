import os
from receipt_label.submit_embedding_batch import (
    list_receipt_words_with_no_embeddings,
    chunk_into_embedding_batches,
    serialize_receipt_words,
    upload_serialized_words,
    query_receipt_words,
    download_serialized_words,
    deserialize_receipt_words,
    format_word_context_embedding,
    write_ndjson,
    upload_to_openai,
    submit_openai_batch,
    create_batch_summary,
    update_word_embedding_status,
    generate_batch_id,
    add_batch_summary,
)
from receipt_label.poll_embedding_batch import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    get_receipt_descriptions,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
)
from receipt_label.utils.clients import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()

S3_BUCKET = os.getenv("S3_BUCKET")
DYNAMO_TABLE_NAME = os.getenv("DYNAMO_TABLE_NAME")


# ------------------------------------------------------------------------------
# Submit embedding batch
# ------------------------------------------------------------------------------
# words_without_embeddings = list_receipt_words_with_no_embeddings()
# DEBUG
image_id = "1bfb07b7-aefd-4579-b721-df597471b96b"
receipt_id = 2
(
    _,
    _,
    words_without_embeddings,
    _,
    _,
    _,
) = dynamo_client.getReceiptDetails(image_id=image_id, receipt_id=receipt_id)
print(f"Found {len(words_without_embeddings)} words")
batches = chunk_into_embedding_batches(words_without_embeddings)
image_id = list(batches.keys())[0]
receipt_id = list(batches[image_id].keys())[0]
all_words_in_receipt = query_receipt_words(image_id, receipt_id)
print(
    f"Found {len(all_words_in_receipt)} words in receipt {receipt_id} of image {image_id}"
)
serialized_words = upload_serialized_words(serialize_receipt_words(batches), S3_BUCKET)
# Treat the following as batch 1
event = serialized_words[0]
all_word_in_receipt = query_receipt_words(event["image_id"], event["receipt_id"])
serialized_word_file = download_serialized_words(event)
deserialized_words = deserialize_receipt_words(serialized_word_file)
batch_id = generate_batch_id()
print(f"Generated batch ID: {batch_id}")
formatted_words = format_word_context_embedding(
    all_words_in_receipt, all_words_in_receipt
)
print(f"Formatted {len(formatted_words)} words")
input_file = write_ndjson(batch_id, formatted_words)
print(f"Wrote input file to {input_file}")
openai_file = upload_to_openai(input_file)
print(f"Uploaded input file to OpenAI")
openai_batch = submit_openai_batch(openai_file.id)
print(f"Submitted OpenAI batch {openai_batch.id}")
batch_summary = create_batch_summary(batch_id, openai_batch.id, input_file)
print(f"Created batch summary with ID {batch_summary.batch_id}")
update_word_embedding_status(all_words_in_receipt)
print(f"Updated word embedding status")
add_batch_summary(batch_summary)
print(f"Added batch summary with ID {batch_summary.batch_id}")

# ------------------------------------------------------------------------------
# Poll embedding batch
# ------------------------------------------------------------------------------
# pending_batches = list_pending_embedding_batches()
# print(f"Found {len(pending_batches)} pending embedding batches")

# for batch in pending_batches:
#     print(f"Processing batch {batch.batch_id}")
#     batch_status = get_openai_batch_status(batch.openai_batch_id)
#     print(f"Batch status: {batch_status}")
#     if batch_status == "completed":
#         downloaded_results = download_openai_batch_result(batch.openai_batch_id)
#         print(f"Got {len(downloaded_results)} results")
#         receipt_descriptions = get_receipt_descriptions(downloaded_results)
#         print(f"Got {len(receipt_descriptions)} receipt descriptions")
#         upserted_vectors_count = upsert_embeddings_to_pinecone(
#             downloaded_results, receipt_descriptions
#         )
#         print(f"Upserted {upserted_vectors_count} vectors to Pinecone")
#         embedding_results_count = write_embedding_results_to_dynamo(
#             downloaded_results, receipt_descriptions, batch.batch_id
#         )
#         print(f"Wrote {embedding_results_count} embedding results to DynamoDB")
#         mark_batch_complete(batch.batch_id)
#         print(f"Marked batch {batch.batch_id} as complete")
