import os
from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_dynamo.constants import MerchantValidationStatus
from receipt_label.merchant_validation import (
    get_receipt_details,
    write_receipt_metadata_to_dynamo,
    extract_candidate_merchant_fields,
    query_google_places,
    is_match_found,
    is_valid_google_match,
    validate_match_with_gpt,
    infer_merchant_with_gpt,
    retry_google_search_with_inferred_data,
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
)

logger = getLogger()
logger.setLevel(INFO)

GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def validate_handler(event, context):
    logger.info("Starting validate_single_receipt_handler")
    image_id = event["image_id"]
    receipt_id = event["receipt_id"]
    (
        receipt,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_tags,
        receipt_word_labels,
    ) = get_receipt_details(image_id, receipt_id)
    logger.info(f"Got Receipt details for {image_id} {receipt_id}")

    # Build raw_text as the lines of the receipt
    raw_text = [line.text for line in receipt_lines]

    extracted_data = extract_candidate_merchant_fields(receipt_words)
    # Pass receipt_words so we can extract business name for text search
    results = query_google_places(
        extracted_data,
        GOOGLE_PLACES_API_KEY,
        all_receipt_words=receipt_words,
    )
    logger.info(f"Google Places results: {results}")

    # 1) Check if Google returned any match at all
    is_match = is_match_found(results) and not results.get("name")
    logger.info(f"Is match found: {is_match}")

    if is_match:
        # 2) Validate the Google result
        is_valid = is_valid_google_match(results, extracted_data)
        logger.info(f"Is valid Google match: {is_valid}")
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
            logger.info(f"Validation result: {validated}")
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
                logger.info(f"Inferred: {inferred}")
                # 5) Retry Google with inferred data
                retry = retry_google_search_with_inferred_data(
                    inferred, GOOGLE_PLACES_API_KEY
                )
                logger.info(f"Retry Google result: {retry}")
                if retry:
                    validated = validate_match_with_gpt(inferred, retry)
                    logger.info(f"Validation after retry: {validated}")
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
        logger.info(f"Inferred: {inferred}")
        # 3) Retry Google with inferred data
        retry = retry_google_search_with_inferred_data(inferred, GOOGLE_PLACES_API_KEY)
        logger.info(f"Retry Google result: {retry}")
        if retry:
            validated = validate_match_with_gpt(inferred, retry)
            logger.info(f"Validation after retry: {validated}")
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

    logger.info(f"Persisted ReceiptMetadata: {metadata}")
    # Post-validation sanity check: force NO_MATCH if no merchant name
    if not metadata.merchant_name:
        metadata.validation_status = MerchantValidationStatus.NO_MATCH
        metadata.validated_by = "GPT"
    write_receipt_metadata_to_dynamo(metadata)

    return {
        "statusCode": 200,
        "body": "Hello, World!",
    }
