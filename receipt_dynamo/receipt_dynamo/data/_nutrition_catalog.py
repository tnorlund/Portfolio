"""Immutable food revisions and conditional aliases; all I/O stays in DAL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import FlattenedStandardMixin
from receipt_dynamo.data.base_operations.error_handling import (
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityValidationError,
    NutritionConflictError,
)
from receipt_dynamo.entities.food_product import (
    FoodProduct,
    food_product_key,
    item_to_food_product,
)
from receipt_dynamo.entities.nutrition_support import (
    check_revision,
    nutrition_key,
)
from receipt_dynamo.entities.product_alias import (
    ProductAlias,
    item_to_product_alias,
    product_alias_key,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import (
        PutTypeDef,
        TransactWriteItemTypeDef,
    )


def raise_nutrition_conflict(error: ClientError) -> None:
    """Only failed conditions mean conflicts, not throttles or validation."""
    code = error.response.get("Error", {}).get("Code")
    reasons = error.response.get("CancellationReasons", [])
    if code == "ConditionalCheckFailedException" or (
        code == "TransactionCanceledException"
        and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
        and all(
            r.get("Code") in (None, "None", "ConditionalCheckFailed")
            for r in reasons
        )
    ):
        raise NutritionConflictError(
            "nutrition condition changed or referenced product is missing"
        ) from error


class _NutritionCatalog(FlattenedStandardMixin):
    """No product update/delete or alias delete that could reset revisions."""

    def _assert_nutrition_table(self, expected_table_name: str) -> None:
        if not expected_table_name or expected_table_name != self.table_name:
            raise EntityValidationError("nutrition write table mismatch")

    @handle_dynamodb_errors("add_food_product")
    def add_food_product(
        self, product: FoodProduct, *, expected_table_name: str
    ) -> FoodProduct:
        self._assert_nutrition_table(expected_table_name)
        if not isinstance(product, FoodProduct):
            raise EntityValidationError("item must be a FoodProduct")
        self._client.put_item(
            TableName=self.table_name,
            Item=product.to_item(),
            ConditionExpression="attribute_not_exists(PK)",
        )
        return product

    @handle_dynamodb_errors("get_food_product")
    def get_food_product(
        self, product_id: str, revision: str
    ) -> FoodProduct | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key=food_product_key(product_id, revision),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item_to_food_product(item) if item else None

    @handle_dynamodb_errors("list_food_products")
    def list_food_product_revisions(
        self,
        product_id: str,
        *,
        limit: int = 25,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[FoodProduct], dict[str, Any] | None]:
        items, cursor = self._nutrition_catalog_page(
            f"FOOD_PRODUCT#{nutrition_key(product_id)}",
            limit,
            last_evaluated_key,
        )
        return [item_to_food_product(item) for item in items], cursor

    @handle_dynamodb_errors("get_product_alias")
    def get_product_alias(
        self, merchant_slug: str, kind: str, text: str
    ) -> ProductAlias | None:
        """Return expired records too: callers need their revision to retry."""
        response = self._client.get_item(
            TableName=self.table_name,
            Key=product_alias_key(merchant_slug, kind, text),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item_to_product_alias(item) if item else None

    @handle_dynamodb_errors("save_product_alias")
    def save_product_alias(
        self,
        alias: ProductAlias,
        *,
        expected_revision: int,
        expected_table_name: str,
    ) -> ProductAlias:
        self._assert_nutrition_table(expected_table_name)
        if not isinstance(alias, ProductAlias):
            raise EntityValidationError("item must be a ProductAlias")
        check_revision(expected_revision, minimum=0)
        item = alias.to_item()
        if alias.revision != expected_revision + 1:
            raise EntityValidationError("alias revision must increment by one")
        condition = "attribute_not_exists(PK)"
        values: dict[str, Any] = {}
        names: dict[str, str] = {}
        if expected_revision:
            condition = "#revision = :expected"
            names["#revision"] = "revision"
            values[":expected"] = {"N": str(expected_revision)}
        if not alias.confirmed_by_user:
            condition += (
                " AND (attribute_not_exists(confirmed_by_user)"
                " OR confirmed_by_user = :false)"
            )
            values[":false"] = {"BOOL": False}
        put: PutTypeDef = {
            "TableName": self.table_name,
            "Item": item,
            "ConditionExpression": condition,
        }
        if names:
            put["ExpressionAttributeNames"] = names
        if values:
            put["ExpressionAttributeValues"] = values
        transaction: list[TransactWriteItemTypeDef] = [{"Put": put}]
        if alias.status == "matched":
            assert alias.product_id is not None
            assert alias.product_revision is not None
            transaction.append(
                {
                    "ConditionCheck": {
                        "TableName": self.table_name,
                        "Key": food_product_key(
                            alias.product_id, alias.product_revision
                        ),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
            )
        try:
            self._client.transact_write_items(TransactItems=transaction)
        except ClientError as error:
            raise_nutrition_conflict(error)
            raise
        return alias

    @handle_dynamodb_errors("list_product_aliases")
    def list_product_aliases(
        self,
        merchant_slug: str,
        *,
        limit: int = 25,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[ProductAlias], dict[str, Any] | None]:
        items, cursor = self._nutrition_catalog_page(
            f"PRODUCT_ALIAS#{nutrition_key(merchant_slug)}",
            limit,
            last_evaluated_key,
        )
        return [item_to_product_alias(item) for item in items], cursor

    def _nutrition_catalog_page(
        self,
        partition: str,
        limit: int,
        cursor: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise EntityValidationError("nutrition page limit must be 1..100")
        query: dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": {":pk": {"S": partition}},
            "Limit": limit,
            "ConsistentRead": True,
        }
        if cursor:
            if (
                not isinstance(cursor, dict)
                or cursor.get("PK") != {"S": partition}
                or not isinstance(cursor.get("SK", {}).get("S"), str)
                or set(cursor) != {"PK", "SK"}
            ):
                raise EntityValidationError("nutrition cursor scope mismatch")
            query["ExclusiveStartKey"] = cursor
        response = self._client.query(**query)
        return response.get("Items", []), response.get("LastEvaluatedKey")
