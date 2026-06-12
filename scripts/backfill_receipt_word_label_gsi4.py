#!/usr/bin/env python3
"""Backfill GSI4PK/GSI4SK on RECEIPT_WORD_LABEL items missing them.

The Swift Mac worker historically wrote ReceiptWordLabel items without GSI4
keys, making them invisible to get_receipt_details (which queries GSI4).
This script scans the table and patches any such items.
"""
import boto3

TABLE = "ReceiptsTable-dc5be22"
ddb = boto3.client("dynamodb")


def parse_sk(sk: str):
    # SK = RECEIPT#00001#LINE#00002#WORD#00003#LABEL#ADDRESS_LINE
    parts = sk.split("#")
    return {
        "receipt_id": int(parts[1]),
        "line_id": int(parts[3]),
        "word_id": int(parts[5]),
        "label": parts[7],
    }


def main():
    patched = 0
    already_ok = 0
    scanned = 0
    paginator = ddb.get_paginator("scan")
    page_iter = paginator.paginate(
        TableName=TABLE,
        FilterExpression="#t = :t AND attribute_not_exists(GSI4PK)",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_WORD_LABEL"}},
        ProjectionExpression="PK, SK",
    )
    for page in page_iter:
        for item in page.get("Items", []):
            scanned += 1
            pk = item["PK"]["S"]  # IMAGE#<uuid>
            sk = item["SK"]["S"]
            image_id = pk.split("#", 1)[1]
            meta = parse_sk(sk)
            gsi4pk = f"IMAGE#{image_id}#RECEIPT#{meta['receipt_id']:05d}"
            gsi4sk = f"4_LABEL#{meta['line_id']:05d}#{meta['word_id']:05d}#{meta['label']}"
            ddb.update_item(
                TableName=TABLE,
                Key={"PK": {"S": pk}, "SK": {"S": sk}},
                UpdateExpression="SET GSI4PK = :pk, GSI4SK = :sk",
                ConditionExpression="attribute_not_exists(GSI4PK)",
                ExpressionAttributeValues={
                    ":pk": {"S": gsi4pk},
                    ":sk": {"S": gsi4sk},
                },
            )
            patched += 1
            if patched % 25 == 0:
                print(f"  patched {patched}/{scanned}")
    print(f"\nDONE — scanned {scanned} items missing GSI4PK, patched {patched}")


if __name__ == "__main__":
    main()
