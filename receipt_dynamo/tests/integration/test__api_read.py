"""Exercise the lean API reader against real index and cursor semantics."""

from typing import Literal
from uuid import UUID

import pytest

from receipt_dynamo import DynamoClient, Image
from receipt_dynamo.api_read import ApiDynamoClient


@pytest.mark.integration
def test_index_pages_preserve_limits_and_cursor(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    writer = DynamoClient(dynamodb_table)
    expected = set()
    for index in range(8):
        image_id = str(UUID(int=index + 1, version=4))
        kind = "PHOTO" if index < 5 else "SCAN"
        if kind == "PHOTO":
            expected.add(image_id)
        writer.add_image(
            Image(
                image_id=image_id,
                width=100,
                height=200,
                timestamp_added="2026-09-08T00:00:00+00:00",
                raw_s3_bucket="offline",
                raw_s3_key=f"{index}.png",
                image_type=kind,
                receipt_count=index,
            )
        )
    reader = ApiDynamoClient(dynamodb_table, writer._client)
    seen = []
    cursor = None
    for _ in range(4):
        page, cursor = reader.list_images_by_type(
            "PHOTO", limit=2, last_evaluated_key=cursor
        )
        assert len(page) <= 2
        seen.extend(image["image_id"] for image in page)
        if cursor is None:
            break
    else:
        pytest.fail("cursor did not terminate")
    assert len(seen) == len(expected)
    assert set(seen) == expected
