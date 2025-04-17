from receipt_label.poll_embedding_batch import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
)
from receipt_label.utils import get_clients

# dynamo_client, openai_client, pinecone_index = get_clients()

openai_batch_id = "batch_6800788e13508190ac9a517d45c2824d"
batch_id = "6ad1dad7-01fc-473a-ace6-8cb7fa7c48da"

batch_status = get_openai_batch_status(openai_batch_id)
if batch_status == "completed":
    embeddings = download_openai_batch_result(openai_batch_id)
    print(f"Downloaded {len(embeddings)} embeddings from OpenAI")
    upsert_embeddings_to_pinecone(embeddings)
    write_embedding_results_to_dynamo(embeddings, batch_id)
