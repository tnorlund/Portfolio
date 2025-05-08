import json
from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.merchant_validation import list_receipts_for_merchant_validation

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


def list_handler(event, context):
    logger.info("Starting list_receipts_handler")
    receipts = list_receipts_for_merchant_validation()
    logger.info(f"Found {len(receipts)} receipts for merchant validation")
    return {
        "statusCode": 200,
        "receipts": [
            {
                "image_id": receipt[0],
                "receipt_id": receipt[1],
            }
            for receipt in receipts
        ],
    }
