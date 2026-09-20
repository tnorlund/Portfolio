"""Translate validated domain facts to independent DAL storage records."""

from receipt_dynamo.entities.food_product import FoodProduct
from receipt_dynamo.entities.nutrition_support import nutrition_json

from receipt_nutrition.models import Product


def product_record(product: Product) -> FoodProduct:
    return FoodProduct(
        product_id=product.product_id,
        payload_json=nutrition_json(product.model_dump(mode="python")),
    )


def product_from_record(record: FoodProduct) -> Product:
    product = Product.model_validate_json(record.payload_json)
    if product.product_id != record.product_id:
        raise ValueError("stored product identity mismatch")
    return product
