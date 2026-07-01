"""Locale-parameterized number/date regex atoms for the receipt renderer.

The price and date token patterns were duplicated across receipt_renderer.py,
receipt_grid.py, font_profile.py and content_clean.py, each hardcoding the US
conventions (``$`` currency, ``,`` thousands, ``.`` decimal, 2 fraction digits,
a trailing ``[A-Z]`` tax flag, M/D/Y date order). This module declares those
conventions ONCE as a :class:`NumberFormat`; each site keeps its own structure
(sign handling, anchoring, capture groups) but builds the locale-specific pieces
from these atoms, so supporting a non-US merchant is a config change rather than
an edit to every regex. ``US`` reproduces the exact former patterns byte-for-byte
(see tests/test_number_format.py).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NumberFormat:
    """Regex-level description of a merchant's number/date conventions.

    All string fields are already regex-escaped (``\\$``, ``\\.``) so they drop
    straight into a pattern.
    """

    currency: str = r"\$"
    thousands: str = r","
    decimal: str = r"\."
    fraction_digits: int = 2
    tax_flag: str = r"[A-Z]"
    date_order: str = "MDY"  # token order: MDY | DMY | YMD


US = NumberFormat()


def _frac(fmt: NumberFormat) -> str:
    return rf"\d{{{fmt.fraction_digits}}}"


def integer_part(fmt: NumberFormat, *, allow_bare: bool = False) -> str:
    """The integer portion of a money amount.

    ``allow_bare=False`` -> ``\\d{1,3}(?:,\\d{3})*`` (grouped, optional groups).
    ``allow_bare=True``  -> ``(?:\\d{1,3}(?:,\\d{3})+|\\d+)`` (grouped-or-plain).
    """
    grouped = rf"\d{{1,3}}(?:{fmt.thousands}\d{{3}})"
    if allow_bare:
        return rf"(?:{grouped}+|\d+)"
    return rf"{grouped}*"


def fraction(fmt: NumberFormat) -> str:
    """The decimal separator + fraction digits, e.g. ``\\.\\d{2}``."""
    return rf"{fmt.decimal}{_frac(fmt)}"


def date_core(fmt: NumberFormat = US, *, groups: bool = False) -> str:
    """The date token body (no anchors), e.g. ``\\d{1,2}/\\d{1,2}/\\d{2,4}``.

    Field widths follow the token order; ``groups=True`` wraps each field in a
    capture group (for parsing month/day/year out).
    """
    day = month = r"\d{1,2}"
    year = r"\d{2,4}"
    if groups:
        day = month = r"(\d{1,2})"
        year = r"(\d{2,4})"
    order = {
        "MDY": (month, day, year),
        "DMY": (day, month, year),
        "YMD": (year, month, day),
    }[fmt.date_order]
    return "/".join(order)
