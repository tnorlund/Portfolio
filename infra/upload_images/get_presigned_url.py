import json
import os
import urllib.parse

import boto3

BUCKET_NAME = os.environ["BUCKET_NAME"]
s3 = boto3.client("s3")


def handler(event, context):
    try:
        query = event.get("queryStringParameters") or {}
        content_type = query.get("contentType", "image/jpeg")
        raw_filename = query.get("filename", "")
        if not raw_filename:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing filename"}),
            }

        filename = urllib.parse.unquote(raw_filename)

        key = f"raw-receipts/{filename}"

        url = s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=60,
        )

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"url": url, "key": key}),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }
