"""Revisioned, merchant-scoped product resolution with explicit expiry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.nutrition_support import (
    check_nutrition_hash,
    check_revision,
    nutrition_hash,
    nutrition_item,
    nutrition_json,
    nutrition_key,
    nutrition_values,
    read_nutrition_json,
)

ALIAS_STATUSES = {"matched", "pending", "no_match", "not_food", "rejected"}


def product_alias_key(
    merchant_slug: str, kind: str, text: str
) -> dict[str, Any]:
    if kind not in ("TEXT", "ITEM"):
        raise EntityValidationError("alias kind must be TEXT or ITEM")
    return {
        "PK": {"S": f"PRODUCT_ALIAS#{nutrition_key(merchant_slug)}"},
        "SK": {"S": f"{kind}#{nutrition_key(text)}"},
    }


@dataclass(eq=True)
class ProductAlias(DynamoDBEntity):
    merchant_slug: str
    kind: str
    text: str
    revision: int
    status: str
    method: str
    changed_at: str
    applicability_json: str
    decision_json: str = "{}"
    product_id: str | None = None
    product_revision: str | None = None
    confirmed_by_user: bool = False
    expires_at: int | None = None

    def __post_init__(self) -> None:
        product_alias_key(self.merchant_slug, self.kind, self.text)
        check_revision(self.revision)
        if self.status not in ALIAS_STATUSES:
            raise EntityValidationError("invalid alias status")
        if self.method not in ("user", "identifier", "lexical", "model"):
            raise EntityValidationError("invalid alias method")
        if type(self.confirmed_by_user) is not bool or (
            self.confirmed_by_user != (self.method == "user")
        ):
            raise EntityValidationError("user decisions require user method")
        if self.status == "matched":
            if self.product_id is None or self.product_revision is None:
                raise EntityValidationError("matched alias requires product")
            nutrition_key(self.product_id)
            check_nutrition_hash(self.product_revision)
        elif self.product_id is not None or self.product_revision is not None:
            raise EntityValidationError("only matched aliases pin a product")
        if self.confirmed_by_user:
            if self.expires_at is not None:
                raise EntityValidationError("user decisions must not expire")
        else:
            if self.expires_at is None:
                raise EntityValidationError("automatic alias requires expiry")
            check_revision(self.expires_at)
        try:
            timestamp = datetime.fromisoformat(self.changed_at)
            if not self.changed_at.endswith("+00:00"):
                raise ValueError("UTC convention")
            if self.expires_at is not None and (
                self.expires_at <= timestamp.timestamp()
            ):
                raise ValueError("expiry precedes decision")
        except (TypeError, ValueError) as error:
            raise EntityValidationError("invalid alias timestamps") from error
        scope = read_nutrition_json(self.applicability_json)
        if not scope:
            raise EntityValidationError("alias applicability cannot be empty")
        self.applicability_json = nutrition_json(scope)
        self.decision_json = nutrition_json(
            read_nutrition_json(self.decision_json)
        )

    @property
    def key(self) -> dict[str, Any]:
        return product_alias_key(self.merchant_slug, self.kind, self.text)

    @property
    def alias_id(self) -> str:
        return nutrition_hash(self.key)

    def is_expired(self, now: int) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def to_item(self) -> dict[str, Any]:
        self.__post_init__()
        # Keep expired rows so revisions never reset after eventual TTL
        # removal. Expiry is application-enforced; this is not a TTL field.
        return {
            **self.key,
            **nutrition_item({"TYPE": "PRODUCT_ALIAS", **self.to_dict()}),
        }


def item_to_product_alias(item: dict[str, Any]) -> ProductAlias:
    values = nutrition_values(item)
    try:
        pk, sk, record_type = (
            values.pop("PK"),
            values.pop("SK"),
            values.pop("TYPE"),
        )
        revision = values["revision"]
        if revision != int(revision):
            raise EntityValidationError("noninteger stored alias revision")
        values["revision"] = int(revision)
        if values.get("expires_at") is not None:
            expiry = values["expires_at"]
            if expiry != int(expiry):
                raise EntityValidationError("noninteger stored expiry")
            values["expires_at"] = int(expiry)
        alias = ProductAlias(**values)
        if record_type != "PRODUCT_ALIAS" or alias.key != {
            "PK": {"S": pk},
            "SK": {"S": sk},
        }:
            raise EntityValidationError("alias key integrity failure")
        return alias
    except (KeyError, TypeError, ValueError) as error:
        raise EntityValidationError("invalid product alias record") from error
