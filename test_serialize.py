import json
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


receipt_id = 2
image_id = "1bfb07b7-aefd-4579-b721-df597471b96b"
(
    _,
    _,
    words_without_embeddings,
    _,
    _,
    _,
) = dynamo_client.getReceiptDetails(image_id=image_id, receipt_id=receipt_id)
for word in words_without_embeddings[-5:-1]:
    print(word.line_id, word.word_id, word.text)

print(f"Found {len(words_without_embeddings)} words")
serialized_words = serialize_receipt_words(
    {image_id: {receipt_id: words_without_embeddings}}
)
print(f"Serialized words to: {serialized_words[0]['ndjson_path']} files")
# Count the lines in the serialized words
for serialized_word in serialized_words:
    with open(serialized_word["ndjson_path"], "r") as f:
        print(f"File {serialized_word['ndjson_path']} has {sum(1 for line in f)} lines")

    deserialized_words = deserialize_receipt_words(serialized_word["ndjson_path"])
    print(f"Deserialized words: {len(deserialized_words)}")

failing_file = "/Users/tnorlund/Downloads/1bfb07b7-aefd-4579-b721-df597471b96b_2_707e2398-5304-42b7-9924-743bd5cb41a6.ndjson"
failing_deserialized_words = deserialize_receipt_words(failing_file)
print(f"Deserialized words: {len(failing_deserialized_words)}")

# turn each dict into a sorted JSON string
orig_set = {json.dumps(dict(w), sort_keys=True) for w in deserialized_words}
fail_set = {json.dumps(dict(w), sort_keys=True) for w in failing_deserialized_words}

# words in the failing file but not in the original
extra_jsons = fail_set - orig_set
extra_words = [json.loads(s) for s in extra_jsons]

print(f"Extra words found: {len(extra_words)}")
# for w in extra_words:
#     print(w)
