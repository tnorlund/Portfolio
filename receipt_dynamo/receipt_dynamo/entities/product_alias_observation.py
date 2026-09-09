"""Per-line pointers recording which alias revision decided an outcome."""

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
    nutrition_key,
    nutrition_values,
)
from receipt_dynamo.entities.product_alias import (
    ALIAS_STATUSES,
    product_alias_key,
)
from receipt_dynamo.entities.receipt_nutrition import nutrition_receipt_key

OBSERVATION_TYPE = "PRODUCT_ALIAS_OBSERVATION"


def product_alias_id(merchant_slug: str, kind: str, text: str) -> str:
    """The alias identity a pointer partition is keyed on."""
    return nutrition_hash(product_alias_key(merchant_slug, kind, text))


def alias_observation_key(
    alias_id: str, image_id: str, receipt_id: int, item_index: int
) -> dict[str, Any]:
    check_nutrition_hash(alias_id)
    nutrition_receipt_key(image_id, receipt_id)
    if type(item_index) is not int or not 0 <= item_index <= 99999:
        raise EntityValidationError("invalid alias observation item index")
    return {
        "PK": {"S": f"ALIAS_OBS#{alias_id}"},
        "SK": {"S": f"RECEIPT#{image_id}#{receipt_id:05d}#{item_index:05d}"},
    }


@dataclass(eq=True)
class ProductAliasObservation(DynamoDBEntity):
    """One row per line per alias key; ``alias_revision`` 0 means no row."""

    alias_id: str
    merchant_slug: str
    kind: str
    text: str
    status: str
    alias_revision: int
    image_id: str
    receipt_id: int
    item_index: int
    observed_at: str
    product_id: str | None = None
    product_revision: str | None = None

    def __post_init__(self) -> None:
        alias_observation_key(
            self.alias_id, self.image_id, self.receipt_id, self.item_index
        )
        if self.alias_id != product_alias_id(
            self.merchant_slug, self.kind, self.text
        ):
            raise EntityValidationError("alias observation identity mismatch")
        if self.status not in ALIAS_STATUSES:
            raise EntityValidationError("invalid alias status")
        check_revision(self.alias_revision, minimum=0)
        if self.status == "matched":
            if self.product_id is None or self.product_revision is None:
                raise EntityValidationError("matched outcome requires product")
            nutrition_key(self.product_id)
            check_nutrition_hash(self.product_revision)
        elif self.product_id is not None or self.product_revision is not None:
            raise EntityValidationError("only matched outcomes pin a product")
        try:
            datetime.fromisoformat(self.observed_at)
            if not self.observed_at.endswith("+00:00"):
                raise ValueError("UTC convention")
        except (TypeError, ValueError) as error:
            raise EntityValidationError(
                "invalid alias observation timestamp"
            ) from error

    @property
    def key(self) -> dict[str, Any]:
        return alias_observation_key(
            self.alias_id, self.image_id, self.receipt_id, self.item_index
        )

    @property
    def alias_key(self) -> dict[str, Any]:
        return product_alias_key(self.merchant_slug, self.kind, self.text)

    def to_item(self) -> dict[str, Any]:
        self.__post_init__()
        return nutrition_item(
            {"TYPE": OBSERVATION_TYPE, **self.to_dict()}, key=self.key
        )


def item_to_product_alias_observation(
    item: dict[str, Any],
) -> ProductAliasObservation:
    values = nutrition_values(item)
    try:
        pk, sk, record_type = (
            values.pop("PK"),
            values.pop("SK"),
            values.pop("TYPE"),
        )
        for name in ("alias_revision", "receipt_id", "item_index"):
            number = values[name]
            if number != int(number):
                raise EntityValidationError(f"noninteger stored {name}")
            values[name] = int(number)
        observation = ProductAliasObservation(**values)
    except EntityValidationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise EntityValidationError(
            "invalid product alias observation record"
        ) from error
    if record_type != OBSERVATION_TYPE or observation.key != {
        "PK": {"S": pk},
        "SK": {"S": sk},
    }:
        raise EntityValidationError("alias observation key integrity")
    return observation
