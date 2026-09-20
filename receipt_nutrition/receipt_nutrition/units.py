"""Explicit unit conversion; no mass/volume/count equivalence by default."""

from decimal import Decimal, localcontext
from fractions import Fraction

from receipt_nutrition.models import Amount, Product, Unit, decimal_input

# US customary volume, not imperial. These are exact conversion constants.
UNIT_FACTORS: dict[str, tuple[Unit, Decimal]] = {
    "g": ("g", Decimal("1")),
    "kg": ("g", Decimal("1000")),
    "lb": ("g", Decimal("453.59237")),
    "oz": ("g", Decimal("28.349523125")),
    "ml": ("ml", Decimal("1")),
    "l": ("ml", Decimal("1000")),
    "fl oz": ("ml", Decimal("29.5735295625")),
    "each": ("each", Decimal("1")),
}


def normalized_amount(value: str | int | Decimal, unit: str) -> Amount:
    """Only explicitly supported units may enter the canonical shape."""
    if unit not in UNIT_FACTORS:
        raise ValueError(f"unsupported physical unit: {unit}")
    canonical, factor = UNIT_FACTORS[unit]
    number = decimal_input(value)
    with localcontext() as context:
        context.prec = max(50, len(number.as_tuple().digits) + 20)
        return Amount(value=number * factor, unit=canonical)


def verified_source(product: Product, reference: str | None) -> bool:
    return any(
        source.evidence_id == reference and source.verification != "unverified"
        for source in product.evidence
    )


def exact_amount_in(
    amount: Amount | None, target: Unit, product: Product
) -> Fraction | None:
    """Convert dimensions only with sourced density/mass-per-item facts."""
    if amount is None:
        return None
    if amount.unit == target:
        return Fraction(amount.value)
    if not verified_source(product, product.conversion_source_ref):
        return None
    mass = (
        Fraction(product.mass_per_each_g)
        if product.mass_per_each_g is not None
        else None
    )
    density = (
        Fraction(product.density_g_per_ml)
        if product.density_g_per_ml is not None
        else None
    )
    if amount.unit == "g":
        grams = Fraction(amount.value)
    elif amount.unit == "ml" and density is not None:
        grams = Fraction(amount.value) * density
    elif amount.unit == "each" and mass is not None:
        grams = Fraction(amount.value) * mass
    else:
        return None
    if target == "g":
        return grams
    if target == "ml" and density is not None:
        return grams / density
    if target == "each" and mass is not None:
        return grams / mass
    return None


def as_decimal(value: Fraction) -> Decimal:
    """Round only display totals; monetary decisions use the exact ratio."""
    with localcontext() as context:
        context.prec = 50
        return Decimal(value.numerator) / Decimal(value.denominator)


def amount_in(
    amount: Amount | None, target: Unit, product: Product
) -> Decimal | None:
    exact = exact_amount_in(amount, target, product)
    return as_decimal(exact) if exact is not None else None
