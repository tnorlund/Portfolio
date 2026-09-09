"""Append-only product facts, addressed by stable identity and content hash."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.nutrition_support import (
    check_nutrition_hash,
    nutrition_hash,
    nutrition_item,
    nutrition_json,
    nutrition_key,
    nutrition_values,
    read_nutrition_json,
)


def food_product_key(product_id: str, revision: str) -> dict[str, Any]:
    check_nutrition_hash(revision)
    return {
        "PK": {"S": f"FOOD_PRODUCT#{nutrition_key(product_id)}"},
        "SK": {"S": f"REV#{revision}"},
    }


@dataclass(eq=True)
class FoodProduct(DynamoDBEntity):
    product_id: str
    payload_json: str

    def __post_init__(self) -> None:
        nutrition_key(self.product_id)
        payload = read_nutrition_json(self.payload_json)
        if payload.get("product_id") != self.product_id:
            raise EntityValidationError("product payload identity mismatch")
        self.payload_json = nutrition_json(payload)

    @property
    def revision(self) -> str:
        return nutrition_hash(read_nutrition_json(self.payload_json))

    @property
    def key(self) -> dict[str, Any]:
        return food_product_key(self.product_id, self.revision)

    def to_item(self) -> dict[str, Any]:
        # Revalidate mutable dataclass fields at the storage boundary.
        self.__post_init__()
        return {
            **self.key,
            **nutrition_item(
                {
                    "TYPE": "FOOD_PRODUCT",
                    "product_id": self.product_id,
                    "revision": self.revision,
                    "payload_json": self.payload_json,
                }
            ),
        }


def item_to_food_product(item: dict[str, Any]) -> FoodProduct:
    values = nutrition_values(item)
    try:
        product = FoodProduct(values["product_id"], values["payload_json"])
        if (
            values["TYPE"] != "FOOD_PRODUCT"
            or values["revision"] != product.revision
            or {key: item[key] for key in ("PK", "SK")} != product.key
        ):
            raise EntityValidationError("product revision integrity failure")
        return product
    except (KeyError, TypeError) as error:
        raise EntityValidationError("invalid food product record") from error
