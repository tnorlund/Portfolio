"""Small DynamoDB query client for latency-sensitive read-only API routes.

This data-layer module is packaged directly as ``_api_dynamo.py`` in the two
route archives. It avoids the general client's entity imports and DescribeTable
call while keeping every DynamoDB operation owned by receipt_dynamo. It must
remain importable without the package initializer or sibling dependencies.
"""

from typing import Any, Callable

from botocore.exceptions import ClientError
from botocore.session import Session

# Both files are shipped verbatim in the route archive, with no package init.
if __package__:
    from .data import shared_exceptions as errors
else:
    import _api_dynamo_errors as errors

Item = dict[str, Any]
_ERROR_TYPES = {
    "ValidationException": errors.EntityValidationError,
    "ResourceNotFoundException": errors.OperationError,
    "ProvisionedThroughputExceededException": errors.DynamoDBThroughputError,
    "ThrottlingException": errors.DynamoDBThroughputError,
    "InternalServerError": errors.DynamoDBServerError,
    "ServiceUnavailable": errors.DynamoDBServerError,
}

CDN_FIELDS = (
    "sha256",
    "cdn_s3_bucket",
    "cdn_s3_key",
    "cdn_webp_s3_key",
    "cdn_avif_s3_key",
    "cdn_thumbnail_s3_key",
    "cdn_thumbnail_webp_s3_key",
    "cdn_thumbnail_avif_s3_key",
    "cdn_small_s3_key",
    "cdn_small_webp_s3_key",
    "cdn_small_avif_s3_key",
    "cdn_medium_s3_key",
    "cdn_medium_webp_s3_key",
    "cdn_medium_avif_s3_key",
)

_clients = {}


def _string(item: Item, field: str, default: str | None = None) -> str | None:
    return item.get(field, {}).get("S", default)


def _integer(item: Item, field: str) -> int | None:
    value = item.get(field, {}).get("N")
    return int(value) if value is not None else None


def _point(item: Item, field: str) -> dict[str, float]:
    return {key: float(value["N"]) for key, value in item[field]["M"].items()}


def _cdn_fields(item: Item) -> dict[str, str | None]:
    return {field: _string(item, field) for field in CDN_FIELDS}


def image_to_api(item: Item) -> Item:
    """Convert one raw DynamoDB image item to the public API shape."""
    return {
        **_cdn_fields(item),
        "image_id": item["PK"]["S"].split("#", 1)[1],
        "width": _integer(item, "width"),
        "height": _integer(item, "height"),
        "timestamp_added": _string(item, "timestamp_added"),
        "raw_s3_bucket": _string(item, "raw_s3_bucket"),
        "raw_s3_key": _string(item, "raw_s3_key"),
        "image_type": _string(item, "image_type", "SCAN"),
        "receipt_count": _integer(item, "receipt_count"),
    }


def receipt_to_api(item: Item) -> Item:
    """Convert one raw DynamoDB receipt item to the public API shape."""
    return {
        **_cdn_fields(item),
        "image_id": item["PK"]["S"].split("#", 1)[1],
        "receipt_id": int(item["SK"]["S"].split("#", 1)[1]),
        "width": _integer(item, "width"),
        "height": _integer(item, "height"),
        "timestamp_added": _string(item, "timestamp_added"),
        "raw_s3_bucket": _string(item, "raw_s3_bucket"),
        "raw_s3_key": _string(item, "raw_s3_key"),
        "top_left": _point(item, "top_left"),
        "top_right": _point(item, "top_right"),
        "bottom_left": _point(item, "bottom_left"),
        "bottom_right": _point(item, "bottom_right"),
    }


class ApiDynamoClient:
    """Read-only client exposing only the API routes' indexed queries."""

    def __init__(self, table_name: str, dynamodb_client: Any = None) -> None:
        self.table_name = table_name
        self._client = dynamodb_client or Session().create_client(
            "dynamodb", region_name="us-east-1"
        )

    def _query(
        self,
        parameters: Item,
        converter: Callable[[Item], Item],
        limit: int | None,
        last_evaluated_key: Item | None,
    ) -> tuple[list[Item], Item | None]:
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
        ):
            raise errors.EntityValidationError(
                "limit must be a positive integer"
            )
        results = []
        current_key = last_evaluated_key
        while True:
            request = {
                "TableName": self.table_name,
                **parameters,
            }
            if current_key:
                request["ExclusiveStartKey"] = current_key
            if limit is not None:
                request["Limit"] = limit - len(results)

            try:
                response = self._client.query(**request)
            except ClientError as exc:
                error_type = _ERROR_TYPES.get(
                    exc.response["Error"]["Code"], errors.DynamoDBError
                )
                raise error_type("API DynamoDB query failed") from exc
            results.extend(
                converter(item) for item in response.get("Items", [])
            )
            next_key = response.get("LastEvaluatedKey")
            if limit is not None and len(results) >= limit:
                return results[:limit], next_key
            if not next_key:
                return results, None
            current_key = next_key

    def list_images_by_type(
        self,
        image_type: str,
        limit: int | None = None,
        last_evaluated_key: Item | None = None,
    ) -> tuple[list[Item], Item | None]:
        if image_type not in {"PHOTO", "SCAN"}:
            raise errors.EntityValidationError(
                "image_type must be PHOTO or SCAN"
            )
        return self._query(
            {
                "IndexName": "GSI3",
                "KeyConditionExpression": "#type = :type",
                "ExpressionAttributeNames": {"#type": "GSI3PK"},
                "ExpressionAttributeValues": {
                    ":type": {"S": f"IMAGE#{image_type}"}
                },
                "ScanIndexForward": False,
            },
            image_to_api,
            limit,
            last_evaluated_key,
        )

    def list_receipts(
        self,
        limit: int | None = None,
        last_evaluated_key: Item | None = None,
    ) -> tuple[list[Item], Item | None]:
        return self._query(
            {
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#type = :type",
                "ExpressionAttributeNames": {"#type": "TYPE"},
                "ExpressionAttributeValues": {":type": {"S": "RECEIPT"}},
            },
            receipt_to_api,
            limit,
            last_evaluated_key,
        )


def get_api_dynamo_client(table_name: str) -> ApiDynamoClient:
    """Return one reusable client per table in the Lambda environment."""
    client = _clients.get(table_name)
    if client is None:
        client = ApiDynamoClient(table_name)
        _clients[table_name] = client
    return client
