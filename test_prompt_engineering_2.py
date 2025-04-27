import json
from receipt_dynamo.entities import ReceiptWord, ReceiptLine
import random

# from receipt_label.submit_embedding_batch.submit_batch import get_hybrid_context
from receipt_label.utils import get_clients
from collections import Counter
import logging

dynamo_client, openai_client, pinecone_index = get_clients()

CORE_LABELS = [
    # Merchant & store info
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    # Location/address (either as one line or broken out)
    "ADDRESS_LINE",  # or, for finer breakdown:
    # "ADDRESS_NUMBER",
    # "STREET_NAME",
    # "CITY",
    # "STATE",
    # "POSTAL_CODE",
    # Transaction info
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",  # if you want to distinguish coupons vs. generic discounts
    # Line‑item fields
    "PRODUCT_NAME",  # or ITEM_NAME
    "QUANTITY",  # or ITEM_QUANTITY
    "UNIT_PRICE",  # or ITEM_PRICE
    "LINE_TOTAL",  # or ITEM_TOTAL
    # Totals & taxes
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",  # or TOTAL
]

image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
receipt_id = 1

(
    receipt,
    lines,
    words,
    letters,
    tags,
    labels,
) = dynamo_client.getReceiptDetails(image_id, receipt_id)
metadata = dynamo_client.getReceiptMetadata(image_id, receipt_id)

# Get all unique labels
unique_labels = set(label.label for label in labels)
