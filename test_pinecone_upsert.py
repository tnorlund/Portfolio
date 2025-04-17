from receipt_label.poll_embedding_batch import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
)

openai_batch_id = "batch_6800436496548190bf150faaaea1d64a"
batch_id = "5ae173bf-8586-469e-9538-f8a0640c699c"

batch_status = get_openai_batch_status(openai_batch_id)
if batch_status == "completed":
    embeddings = download_openai_batch_result(openai_batch_id)
    print(f"Downloaded {len(embeddings)} embeddings from OpenAI")
    upsert_embeddings_to_pinecone(embeddings)
