"""Decimal purchase arithmetic over explicitly evidenced physical bases."""

from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

from receipt_nutrition.models import (
    NUTRIENT_UNITS,
    Amount,
    CostingResult,
    Product,
    Purchase,
    PurchasedNutrient,
)
from receipt_nutrition.units import (
    as_decimal,
    exact_amount_in,
    verified_source,
)

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def ratio_money(value: Fraction) -> Decimal:
    """Exact HALF_UP cents, including ties after repeating intermediate ratios."""
    cents = abs(value) * 100
    whole, remainder = divmod(cents.numerator, cents.denominator)
    rounded = whole + (2 * remainder >= cents.denominator)
    # Build the Decimal from a string: every arithmetic route (× CENT,
    # scaleb) rounds under the active context for very large totals, while
    # construction from text is exact at any magnitude.
    sign = "-" if value < 0 and rounded else ""
    return Decimal(f"{sign}{rounded // 100}.{rounded % 100:02d}")


def cost_purchase(product: Product, purchase: Purchase) -> CostingResult:
    """Compute supported totals, leaving missing facts explicitly absent."""
    with localcontext() as context:
        # Inputs carry at most 40 significant digits, so products of two
        # inputs are exact here and nothing rounds before ratio_money.
        context.prec = 120
        return _cost_purchase(product, purchase)


def _declared_servings_conflict(product: Product) -> bool:
    """True when the label's servings/container contradicts its own sizes.

    Labels round ("about 4.5 servings"), so a tolerance of one serving or
    five percent, whichever is larger, is allowed; beyond that the label is
    inconsistent and no serving-based total is supported.
    """
    if (
        product.servings_per_container is None
        or product.serving is None
        or product.net_amount is None
    ):
        return False
    per_package = exact_amount_in(
        product.net_amount, product.serving.unit, product
    )
    if per_package is None:
        return False
    declared = Fraction(product.servings_per_container)
    derived = per_package / Fraction(product.serving.value)
    tolerance = max(Fraction(1), declared / 20)
    return abs(derived - declared) > tolerance


def _cost_purchase(product: Product, purchase: Purchase) -> CostingResult:
    unavailable = tuple(sorted(NUTRIENT_UNITS))
    if purchase.is_adjustment or purchase.extended_price < 0:
        return CostingResult(
            identity_kind=product.identity_kind,
            quantity_status="excluded",
            unavailable_nutrients=unavailable,
            reasons=("adjustment_or_return",),
        )
    quantity = purchase.quantity.quantity
    if quantity is None:
        return CostingResult(
            identity_kind=product.identity_kind,
            quantity_status=purchase.quantity.status,
            unavailable_nutrients=unavailable,
            reasons=(purchase.quantity.reason,),
        )
    package_known = verified_source(product, product.package_source_ref)
    amount: Amount | None = None
    servings: Fraction | None = None
    derived = False
    servings_conflict = package_known and _declared_servings_conflict(product)
    if quantity.unit == "package":
        if package_known and product.net_amount is not None:
            # Derived, not an input: the exact product may carry more digits
            # than the input cap allows, so bypass the input validator.
            amount = Amount.model_construct(
                value=quantity.value * product.net_amount.value,
                unit=product.net_amount.unit,
            )
        if (
            package_known
            and product.servings_per_container is not None
            and not servings_conflict
        ):
            servings = Fraction(quantity.value) * Fraction(
                product.servings_per_container
            )
    else:
        amount = Amount(value=quantity.value, unit=quantity.unit)
    if (
        servings is None
        and package_known
        and product.serving is not None
        and not servings_conflict
    ):
        serving_amount = exact_amount_in(amount, product.serving.unit, product)
        if serving_amount is not None:
            servings = serving_amount / Fraction(product.serving.value)
            derived = True

    nutrients: list[PurchasedNutrient] = []
    for nutrient in product.nutrients:
        if not verified_source(product, nutrient.source_ref):
            continue
        multiplier: Fraction | None = None
        if nutrient.basis == "serving":
            multiplier = servings
        elif nutrient.basis == "each":
            multiplier = exact_amount_in(amount, "each", product)
        else:
            unit = "g" if nutrient.basis == "100g" else "ml"
            basis_amount = exact_amount_in(amount, unit, product)
            if basis_amount is not None:
                multiplier = basis_amount / 100
        if multiplier is not None:
            nutrients.append(
                PurchasedNutrient(
                    nutrient_id=nutrient.nutrient_id,
                    amount=as_decimal(Fraction(nutrient.amount) * multiplier),
                    unit=nutrient.unit,
                    source_ref=nutrient.source_ref,
                    calculation_basis=nutrient.basis,
                )
            )
    available = {nutrient.nutrient_id for nutrient in nutrients}
    grams = exact_amount_in(amount, "g", product)
    milliliters = exact_amount_in(amount, "ml", product)
    reasons: list[str] = []
    if amount is None:
        reasons.append("unknown_package_amount")
    if servings_conflict:
        reasons.append("servings_per_container_conflict")
    if servings is None:
        reasons.append("unknown_or_incompatible_serving")
    if len(available) < len(NUTRIENT_UNITS):
        reasons.append("incomplete_nutrient_totals")
    return CostingResult(
        identity_kind=product.identity_kind,
        quantity_status="known",
        purchased_amount=amount,
        servings_purchased=(
            as_decimal(servings) if servings is not None else None
        ),
        servings_derived=derived,
        cost_per_serving=(
            ratio_money(Fraction(purchase.extended_price) / servings)
            if servings is not None
            else None
        ),
        cost_per_100g=(
            ratio_money(Fraction(purchase.extended_price) * 100 / grams)
            if grams is not None
            else None
        ),
        cost_per_100ml=(
            ratio_money(Fraction(purchase.extended_price) * 100 / milliliters)
            if milliliters is not None
            else None
        ),
        nutrients=tuple(nutrients),
        unavailable_nutrients=tuple(sorted(set(NUTRIENT_UNITS) - available)),
        reasons=tuple(reasons),
    )
