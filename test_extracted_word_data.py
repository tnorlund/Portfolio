import json
import os
from receipt_label.data.places_api import PlacesAPI
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

receipts, last_evaluated_key = dynamo_client.listReceipts(limit=25)
receipt = receipts[4]

# Get Receipt details
receipt, receipt_lines, receipt_words, receipt_letters, receipt_word_tags = (
    dynamo_client.getReceiptDetails(receipt.image_id, receipt.receipt_id)
)

# Get all words that have extracted data
extracted_words = [word for word in receipt_words if word.extracted_data]

# Print all lines
for line in receipt_lines:
    print(line.text)

# Print the extracted data
for word in extracted_words:
    print(word.text, word.extracted_data)


GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]
DYNAMO_TABLE_NAME = os.environ["DYNAMO_TABLE_NAME"]

places_api = PlacesAPI(
    api_key=GOOGLE_PLACES_API_KEY, dynamo_table_name=DYNAMO_TABLE_NAME
)

# Extract data types
phones = [
    w.extracted_data["value"]
    for w in extracted_words
    if w.extracted_data["type"] == "phone"
]
urls = [
    w.extracted_data["value"]
    for w in extracted_words
    if w.extracted_data["type"] == "url"
]
addresses = [w.text for w in extracted_words if w.extracted_data["type"] == "address"]

if phones:
    print(f"\nLooking up by phone: {phones[0]}")
    result = places_api.search_by_phone(phones[0])
    print("Places API result by phone:", json.dumps(result, indent=2))
elif addresses:
    print(f"\nLooking up by address: {' '.join(addresses)}")
    result = places_api.search_by_address(" ".join(addresses), receipt_words)
    print("Places API result by address:", json.dumps(result, indent=2))
elif urls:
    print(f"\nLooking up by URL: {urls[0]} (note: not implemented in Places API)")
else:
    print("\nNo searchable fields (phone, address, url) found in extracted data.")
