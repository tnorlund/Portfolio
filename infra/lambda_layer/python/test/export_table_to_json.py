import os
import json
from dynamo import DynamoClient

CURRENT_DIR = os.path.dirname(__file__)

IMAGE_ID = "5119873a-5401-4dd1-9669-878dff8b5bd5"

if __name__ == "__main__":
    # 1) Grab data from Dynamo or wherever
    dynamo_client = DynamoClient(os.getenv("DYNAMO_DB_TABLE"))  # Adjust as needed

    # For example, get an image and lines from your DB:
    image, lines, words, word_tags, letters, receipt_details = (
        dynamo_client.getImageDetails(IMAGE_ID)
    )

    receipts = []
    receipt_lines = []
    receipt_words = []
    receipt_word_tags = []
    receipt_letters = []

    for receipt_detail_dict in receipt_details:
        receipts.append(receipt_detail_dict["receipt"])
        receipt_lines.extend(receipt_detail_dict["lines"])
        receipt_words.extend(receipt_detail_dict["words"])
        receipt_word_tags.extend(receipt_detail_dict["word_tags"])
        receipt_letters.extend(receipt_detail_dict["letters"])

    with open(os.path.join(
        CURRENT_DIR, "integration", "JSON", f"{IMAGE_ID}.json"
    ), "w") as f:
        json.dump({
            "images": [{
                **dict(image),
                "timestamp_added": "2021-01-01T00:00:00.000000",
                "raw_s3_bucket": "raw-image-bucket",
                "cdn_s3_bucket": "cdn-bucket",
                }],
            "lines": [
                {
                key: value
                for key, value in dict(line).items()
                if key not in ("num_chars", "histogram")
            }
            for line in lines],
            "words": [
                {
                key: value
                for key, value in dict(word).items()
                if key not in ("num_chars", "histogram")
            }
            for word in words],
            "word_tags": [dict(word_tag) for word_tag in word_tags],
            "letters": [dict(letter) for letter in letters],
            "receipts": [{
                **dict(receipt),
                "timestamp_added": "2021-01-01T00:00:00.000000",
                "raw_s3_bucket": "my-raw-bucket",
                "cdn_s3_bucket": "my-cdn-bucket",
                } for receipt in receipts],
            "receipt_lines": [
                {
                key: value
                for key, value in dict(line).items()
                if key not in ("num_chars", "histogram")
            }
            for line in receipt_lines],
            "receipt_words": [
                {
                key: value
                for key, value in dict(word).items()
                if key not in ("num_chars", "histogram")
            }
            for word in receipt_words],
            "receipt_word_tags": [dict(word_tag) for word_tag in receipt_word_tags],
            "receipt_letters": [dict(letter) for letter in receipt_letters],
        }, f, indent=4)
