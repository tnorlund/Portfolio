import os
import json
from dynamo import DynamoClient
import boto3

CURRENT_DIR = os.path.dirname(__file__)

IMAGE_ID = "5a63a88a-3579-408a-9cbd-031ceb86ffc7"

if __name__ == "__main__":
    # 1) Grab data from Dynamo or wherever
    dynamo_client = DynamoClient(os.getenv("DYNAMO_DB_TABLE"))  # Adjust as needed

    # For example, get an image and lines from your DB:
    image, lines, words, word_tags, letters, receipt_details = (
        dynamo_client.getImageDetails(IMAGE_ID)
    )

    # Get the GPT response from S3
    s3 = boto3.client("s3")
    # List all objects in the /raw directory in the image's raw bucket
    response = s3.list_objects_v2(
        Bucket=image.raw_s3_bucket,
        Prefix=f"raw/{image.id}",
    )
    # Get all the objects with GPT in the name
    gpt_objects = [
        obj
        for obj in response.get("Contents", [])
        if "GPT" in obj["Key"]
    ]
    # Print the GPT response
    for obj in gpt_objects:
        response = s3.get_object(
            Bucket=image.raw_s3_bucket,
            Key=obj["Key"],
        )
        print(f"{obj['Key']}")
        print(response["Body"].read().decode("utf-8"))
        print()

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
