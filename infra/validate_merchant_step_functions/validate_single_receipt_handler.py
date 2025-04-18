from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.merchant_validation import get_receipt_details

logger = getLogger()
logger.setLevel(INFO)

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
        receipt_word_labels,
        receipt_words,
        receipt_word_tags,
        receipt_word_labels,
    ) = get_receipt_details(image_id, receipt_id)
    logger.info(f"Got Receipt details for {image_id} {receipt_id}")
    return {
        "statusCode": 200,
        "body": "Hello, World!",
    }
