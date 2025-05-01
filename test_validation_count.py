from receipt_label.utils.clients import get_clients
from receipt_dynamo.constants import ValidationStatus
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def fetch_label_counts(core_label):
    receipt_word_labels, last_evaluated_key = dynamo_client.getReceiptWordLabelsByLabel(
        label=core_label,
        limit=1000,
    )
    while last_evaluated_key is not None:
        next_receipt_word_labels, last_evaluated_key = (
            dynamo_client.getReceiptWordLabelsByLabel(
                label=core_label,
                limit=1000,
                lastEvaluatedKey=last_evaluated_key,
            )
        )
        receipt_word_labels.extend(next_receipt_word_labels)

    label_counts = {}
    for validation_status in ValidationStatus:
        label_counts[validation_status.value] = sum(
            receipt_word_label.validation_status == validation_status
            for receipt_word_label in receipt_word_labels
        )
    return core_label, label_counts


core_label_counts = {}
with ThreadPoolExecutor(max_workers=8) as executor:
    futures = {
        executor.submit(fetch_label_counts, label): label for label in CORE_LABELS
    }
    for future in as_completed(futures):
        label, counts = future.result()
        core_label_counts[label] = counts

print(core_label_counts)
