import json
import logging
import os
import random

from _api_dynamo import get_api_dynamo_client
from _lambda_profiler import profile_handler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def _list_mixed_images(client, limit, cursor):
    """Page each image category without restarting an exhausted category.

    A null DynamoDB key means either not started or exhausted. Carry explicit
    done flags in the composite cursor so limit=1 can eventually reach scans.
    Existing clients continue to echo the opaque JSON cursor unchanged.
    """
    cursor = cursor or {}
    versioned = cursor.get("version") == 2
    done = {
        kind: bool(cursor.get(f"{kind}Done", False)) if versioned else False
        for kind in ("photo", "scan")
    }
    next_keys = {kind: cursor.get(kind) for kind in done}
    images = []
    if limit is None:
        quotas = {"photo": None, "scan": None}
    elif done["photo"]:
        quotas = {"photo": 0, "scan": limit}
    elif done["scan"]:
        quotas = {"photo": limit, "scan": 0}
    else:
        quotas = {"photo": (limit + 1) // 2, "scan": limit // 2}
    for kind in ("photo", "scan"):
        quota = quotas[kind]
        if done[kind] or quota == 0:
            continue
        page, next_key = client.list_images_by_type(
            image_type=kind.upper(),
            limit=quota,
            last_evaluated_key=next_keys[kind],
        )
        images.extend(page)
        next_keys[kind] = next_key
        done[kind] = next_key is None
        if kind == "photo" and quota is not None and not done["scan"]:
            quotas["scan"] += quota - len(page)
    images = list({image["image_id"]: image for image in images}.values())
    random.shuffle(images)
    next_cursor = (
        None
        if all(done.values())
        else {
            "version": 2,
            **next_keys,
            **{f"{kind}Done": value for kind, value in done.items()},
        }
    )
    return images, next_cursor


@profile_handler
def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        query_params = event.get("queryStringParameters") or {}

        # Check for optional 'image_type' parameter
        image_type = query_params.get("image_type")

        # Check for an optional 'limit'
        limit_param = query_params.get("limit")
        try:
            limit = int(limit_param) if limit_param is not None else None
        except (TypeError, ValueError):
            return {
                "statusCode": 400,
                "body": "limit must be a positive integer",
            }
        if limit is not None and limit <= 0:
            return {
                "statusCode": 400,
                "body": "limit must be a positive integer",
            }

        # Check for an optional 'lastEvaluatedKey'
        last_evaluated_key = None
        if "lastEvaluatedKey" in query_params:
            try:
                last_evaluated_key = json.loads(
                    query_params["lastEvaluatedKey"]
                )
            except json.JSONDecodeError:
                logger.error("Error decoding lastEvaluatedKey; ignoring it.")
                last_evaluated_key = None

        if image_type and image_type not in {"PHOTO", "SCAN"}:
            return {
                "statusCode": 400,
                "body": "image_type must be PHOTO or SCAN",
            }
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            return {
                "statusCode": 400,
                "body": "lastEvaluatedKey must be an object",
            }
        client = get_api_dynamo_client(DYNAMODB_TABLE_NAME)
        if image_type:
            # If image_type is specified, use listImagesByType
            raw_images, lek = client.list_images_by_type(
                image_type=image_type,
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            )
            # Remove duplicates
            seen_ids = set()
            images = []
            for img in raw_images:
                if img["image_id"] not in seen_ids:
                    seen_ids.add(img["image_id"])
                    images.append(img)
        else:
            images, lek = _list_mixed_images(client, limit, last_evaluated_key)

        response_body = {
            "images": [dict(i) for i in images],
            "lastEvaluatedKey": lek,
        }
        return {"statusCode": 200, "body": json.dumps(response_body)}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
