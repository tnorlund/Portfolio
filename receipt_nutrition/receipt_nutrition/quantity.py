"""Recover complete unit/rate annotations without replacing the decoder."""

import re
from decimal import Decimal, localcontext

from receipt_nutrition.costing import money
from receipt_nutrition.models import (
    QuantityEvidence,
    QuantityResolution,
    decimal_input,
)
from receipt_nutrition.units import normalized_amount

UNIT_ALIASES = {
    "lb": "lb",
    "lbs": "lb",
    "1b": "lb",
    "ib": "lb",
    "kg": "kg",
    "oz": "oz",
    "g": "g",
    "ml": "ml",
    "l": "l",
    "fl oz": "fl oz",
    "fl. oz": "fl oz",
    "ea": "each",
    "each": "each",
    "pk": "package",
    "pkg": "package",
    "pack": "package",
    "packages": "package",
}
UNITS_PATTERN = (
    r"fl\.?\s*oz|packages|each|pack|pkg|lbs?|1b|ib|kg|oz|ml|ea|pk|g|l"
)
RATE_HINT = re.compile(rf"(?:{UNITS_PATTERN})\s*@", re.IGNORECASE)
# Full-match one logical annotation line, including any printed price basis
# and extended total. Searching for a numeric suffix can falsely confirm an
# OCR decoder error in fractions, grouped numbers or price denominators.
EXPLICIT_RATE = re.compile(
    rf"(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>{UNITS_PATTERN})\s*@\s*\$?"
    r"(?P<rate>\d+(?:\.\d{2,3})?)"
    r"(?:\s*/\s*(?P<rate_unit>[a-z0-9.]+(?:\s+oz)?))?"
    r"(?:\s+\$?(?P<total>\d+\.\d{2}))?",
    re.IGNORECASE,
)


def _unit(text: str) -> str | None:
    key = re.sub(r"\s+", " ", text.lower())
    if re.fullmatch(r"fl\.?\s*oz", key):
        return "fl oz"
    return UNIT_ALIASES.get(key)


def resolve_explicit_quantity(
    raw_text: str,
    quantity: str | int | Decimal | None,
    unit_price: str | int | Decimal | None,
    extended_price: str | int | Decimal,
) -> QuantityResolution:
    """Require one complete explicit annotation agreeing with stored fields.

    Mixed product-name/quantity lines and unsupported numeric formats abstain.
    The annotation may follow a product name on a separate line. This adapter
    does not attempt to reinterpret OCR tokens or guess missing package counts.
    """
    hints = list(RATE_HINT.finditer(raw_text))
    if not hints:
        return QuantityResolution(status="unknown", reason="no_explicit_unit")
    if len(hints) != 1:
        return QuantityResolution(
            status="conflict", reason="multiple_unit_rates"
        )
    lines = [line for line in raw_text.splitlines() if RATE_HINT.search(line)]
    fragments = [
        line.strip()
        for line in raw_text.splitlines()
        if line not in lines
        and re.fullmatch(r"[+-]?[\d./\s]+", line.strip())
        and re.search(r"\d", line)
        and not re.fullmatch(r"\$?[+-]?\d+\.\d{2}", line.strip())
    ]
    if fragments:
        # A bare "1/" or "2" next to the rate line may be the other half of
        # a split fraction or grouped number. Confirming the rate line alone
        # would silently drop it, so abstain.
        return QuantityResolution(
            status="unknown", reason="adjacent_numeric_fragment"
        )
    match = (
        EXPLICIT_RATE.fullmatch(lines[0].strip()) if len(lines) == 1 else None
    )
    if match is None:
        return QuantityResolution(
            status="unknown", reason="unsupported_unit_expression"
        )
    other_totals = [
        line.strip().removeprefix("$")
        for line in raw_text.splitlines()
        if line not in lines
        and re.fullmatch(r"\$?[+-]?\d+\.\d{2}", line.strip())
    ]
    if (
        (match["total"] or other_totals)
        and "." not in match["rate"]
        and not match["rate_unit"]
    ):
        return QuantityResolution(
            status="unknown", reason="ambiguous_price_sequence"
        )
    if quantity is None or unit_price is None:
        return QuantityResolution(
            status="unknown", reason="missing_decoder_quantity_or_rate"
        )
    count, rate = decimal_input(match["quantity"]), decimal_input(
        match["rate"]
    )
    unit = _unit(match["unit"])
    if unit is None:
        return QuantityResolution(status="unknown", reason="unsupported_unit")
    with localcontext() as context:
        context.prec = 50
        consistent = (
            count > 0
            and rate >= 0
            and count == decimal_input(quantity)
            and rate == decimal_input(unit_price)
            and money(count * rate) == decimal_input(extended_price)
        )
    if match["total"] and decimal_input(match["total"]) != decimal_input(
        extended_price
    ):
        consistent = False
    if any(
        decimal_input(total) != decimal_input(extended_price)
        for total in other_totals
    ):
        consistent = False
    if match["rate_unit"] and _unit(match["rate_unit"]) != unit:
        consistent = False
    if not consistent:
        return QuantityResolution(
            status="conflict", reason="decoder_disagreement"
        )
    if unit == "package":
        evidence = QuantityEvidence(
            value=count, unit="package", method="receipt", reference=match[0]
        )
    else:
        amount = normalized_amount(count, unit)
        evidence = QuantityEvidence(
            value=amount.value,
            unit=amount.unit,
            method="receipt",
            reference=match[0],
        )
    return QuantityResolution(
        status="known",
        reason="explicit_unit_matches_decoder",
        quantity=evidence,
    )
