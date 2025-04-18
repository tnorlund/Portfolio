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
from receipt_label.merchant_validation import (
    extract_candidate_merchant_fields,
    query_google_places,
    is_valid_google_match,
    infer_merchant_with_gpt,
    validate_match_with_gpt,
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
    is_match_found,
    retry_google_search_with_inferred_data,
)
from receipt_label.utils import get_clients
import logging

# Silence the PlacesAPI logger entirely:
logging.getLogger("receipt_label.data.places_api").setLevel(logging.WARNING)
# —or— to completely mute it:
# logging.getLogger("receipt_label.data.places_api").propagate = False

GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]

dynamo_client, openai_client, pinecone_index = get_clients()

receipts, last_evaluated_key = dynamo_client.listReceipts(limit=25)

for receipt in receipts:
    # Get Receipt details
    (
        receipt,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_tags,
        receipt_word_labels,
    ) = dynamo_client.getReceiptDetails(receipt.image_id, receipt.receipt_id)

    # Build raw_text as the lines of the receipt
    raw_text = [line.text for line in receipt_lines]

    extracted_data = extract_candidate_merchant_fields(receipt_words)
    # Pass receipt_words so we can extract business name for text search
    results = query_google_places(
        extracted_data,
        GOOGLE_PLACES_API_KEY,
        all_receipt_words=receipt_words,
    )
    print(f"Google Places results: {results}")

    # 1) Check if Google returned any match at all
    is_match = is_match_found(results)
    print(f"Is match found: {is_match}")

    if is_match:
        # 2) Validate the Google result
        is_valid = is_valid_google_match(results, extracted_data)
        print(f"Is valid Google match: {is_valid}")
        if is_valid:
            metadata = build_receipt_metadata_from_result(
                receipt_id=receipt.receipt_id,
                image_id=receipt.image_id,
                gpt_result={},
                google_place=results,
            )
        else:
            # 3) Validate with GPT the Google result
            validated = validate_match_with_gpt(extracted_data, results)
            print("Validation result:", validated)
            if validated["decision"] == "YES":
                metadata = build_receipt_metadata_from_result(
                    receipt_id=receipt.receipt_id,
                    image_id=receipt.image_id,
                    gpt_result=validated,
                    google_place=results,
                )
            else:
                # 4) Infer with GPT
                inferred = infer_merchant_with_gpt(raw_text, extracted_data)
                print("Inferred:", inferred)
                # 5) Retry Google with inferred data
                retry = retry_google_search_with_inferred_data(
                    inferred, GOOGLE_PLACES_API_KEY
                )
                print("Retry Google result:", retry)
                if retry:
                    validated = validate_match_with_gpt(inferred, retry)
                    print("Validation after retry:", validated)
                    results = retry
                    if validated["decision"] == "YES":
                        metadata = build_receipt_metadata_from_result(
                            receipt_id=receipt.receipt_id,
                            image_id=receipt.image_id,
                            gpt_result=validated,
                            google_place=results,
                        )
                    else:
                        metadata = build_receipt_metadata_from_result_no_match(
                            receipt_id=receipt.receipt_id,
                            image_id=receipt.image_id,
                            gpt_result=inferred,
                        )
                else:
                    metadata = build_receipt_metadata_from_result_no_match(
                        receipt_id=receipt.receipt_id,
                        image_id=receipt.image_id,
                        gpt_result=inferred,
                    )
    else:
        # 2) No initial match: Infer with GPT
        inferred = infer_merchant_with_gpt(raw_text, extracted_data)
        print("Inferred:", inferred)
        # 3) Retry Google with inferred data
        retry = retry_google_search_with_inferred_data(inferred, GOOGLE_PLACES_API_KEY)
        print("Retry Google result:", retry)
        if retry:
            validated = validate_match_with_gpt(inferred, retry)
            print("Validation after retry:", validated)
            results = retry
            if validated["decision"] == "YES":
                metadata = build_receipt_metadata_from_result(
                    receipt_id=receipt.receipt_id,
                    image_id=receipt.image_id,
                    gpt_result=validated,
                    google_place=results,
                )
            else:
                metadata = build_receipt_metadata_from_result_no_match(
                    receipt_id=receipt.receipt_id,
                    image_id=receipt.image_id,
                    gpt_result=inferred,
                )
        else:
            metadata = build_receipt_metadata_from_result_no_match(
                receipt_id=receipt.receipt_id,
                image_id=receipt.image_id,
                gpt_result=inferred,
            )

    print("Persisted ReceiptMetadata:", metadata)
