"""Storage and domain models address the same exact fact revisions."""

from receipt_dynamo.entities.food_product import item_to_food_product

from receipt_nutrition.models import Product
from receipt_nutrition.persistence import product_from_record, product_record


def test_domain_storage_roundtrip(products: dict[str, Product]) -> None:
    for product in products.values():
        record = product_record(product)
        assert record.revision == product.content_hash
        assert (
            product_from_record(item_to_food_product(record.to_item()))
            == product
        )
