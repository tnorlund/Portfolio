#!/usr/bin/env python3
import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def send_sync(
    queue_url: str, collection: str, backup_dir: str | None = None
) -> str:
    sqs = boto3.client("sqs")
    try:
        msg_attrs = {
            "source": {
                "StringValue": "efs_snapshot_sync",
                "DataType": "String",
            },
            "collection": {
                "StringValue": collection,
                "DataType": "String",
            },
        }
        if backup_dir:
            msg_attrs["efs_backup_dir"] = {
                "StringValue": backup_dir,
                "DataType": "String",
            }
        resp = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({}),
            MessageGroupId=f"efs_sync:{collection}",
            MessageAttributes=msg_attrs,
        )
        return resp.get("MessageId", "")
    except (ClientError, BotoCoreError) as e:
        print(f"Failed to enqueue EFS snapshot sync: {e}", file=sys.stderr)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue EFS snapshot sync")
    parser.add_argument(
        "--queue-url",
        required=False,
        default=os.environ.get("LINES_QUEUE_URL"),
        help="SQS queue URL (lines or words). Defaults to $LINES_QUEUE_URL",
    )
    parser.add_argument(
        "--collection",
        required=True,
        choices=["lines", "words"],
        help="Collection to sync from EFS to S3",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Optional EFS backup dir to upload directly (skips copy/lock)",
    )
    args = parser.parse_args()

    if not args.queue_url:
        print("--queue-url or $LINES_QUEUE_URL is required", file=sys.stderr)
        sys.exit(1)

    msg_id = send_sync(args.queue_url, args.collection, args.backup_dir)
    print(
        json.dumps(
            {
                "status": "enqueued",
                "queue_url": args.queue_url,
                "collection": args.collection,
                "message_id": msg_id,
            }
        )
    )


if __name__ == "__main__":
    main()
