"""Sender-domain -> parser registry for inbound email receipts.

Parsers are stdlib-only modules exposing ``parse(path) -> dict`` (or
``parse_eml``) that take an RFC-822 .eml file path and return the shared
receipt schema (dollars as floats, ``grand_total is None`` for non-receipts).
"""
from __future__ import annotations

import importlib
import inspect
import re
from typing import Any, Optional


class ParserContractError(Exception):
    """A parser *integration* / return-contract violation.

    Distinct from a per-message parse failure. Raised for systematic defects —
    a missing or non-callable entry point, a signature that cannot accept the
    single .eml path argument, or a return value that is not the shared receipt
    schema (dict / list-of-dicts / None). These must PROPAGATE so Lambda
    retries and, once exhausted, routes the event to the DLQ, rather than being
    caught per-message and persisted as a false ``parse_error`` reported as
    success. Deliberately not a subclass of ValueError/TypeError so the
    handler's per-message parse catch never swallows it.
    """

# group -> (domain suffixes, module name, entry attr)
GROUPS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "apple": (("email.apple.com", "applepay.apple.com", "orders.apple.com", "apple.com"),
              "parse_apple", "parse"),
    "doordash": (("doordash.com",), "parse_doordash", "parse_eml"),
    "amazon": (("amazon.com",), "parse_amazon", "parse"),
    "venmo": (("venmo.com",), "parse_venmo", "parse_eml"),
    "paypal": (("paypal.com",), "parse_paypal", "parse_eml"),
    "pos-restaurants": (("toasttab.com", "squareup.com", "square.com", "clover.com",
                         "spoton.com"), "parse_pos_restaurants", "parse_eml"),
    "uber": (("uber.com",), "parse_uber", "parse_eml"),
    "retail": (("target.com", "bestbuy.com", "ebay.com", "starbucks.com"),
               "parse_retail", "parse"),
    "equinox": (("equinox.com",), "parse_equinox", "parse"),
    "github": (("github.com",), "parse_github", "parse"),
    "restaurant-platforms": (("chownow.com", "dylish.com", "oftendining.com"),
                             "parse_restaurant_platforms", "parse"),
    "sce": (("scewebservices.com",), "parse_restaurant_platforms", "parse"),
    "services": (("socalgas.com", "digitalocean.com", "stripe.com",
                  "accounts.nintendo.com"), "parse_services", "parse"),
    "chase-alerts": (("chase.com",), "parse_chase_alerts", "parse"),
    "costco": (("costco.com", "costco.com.mx"), "parse_costco", "parse"),
    "travel-housing": (("airbnb.com", "tesla.com", "hellolanding.com",
                        "landing.com"), "parse_travel_housing", "parse"),
}

# Notification/marketing templates whose bodies carry amounts that are NOT
# purchase receipts (kept in sync with the private reconciliation plane).
SUBJECT_DROP = {
    "paypal": re.compile(r"transfer request|requested a hold|hold on the funds|"
                         r"has been (removed|released)|policy|survey", re.I),
    "retail": re.compile(r"is ending soon|relisted item|sent a message|watched item|"
                         r"back in stock|price drop|\bbid\b|offer (received|declined|accepted)|"
                         r"pick up where you left|invite|coupon|% off|\bsale\b|\bdeals?\b", re.I),
    "sce": re.compile(r"bill is ready", re.I),
    "services": re.compile(r"bill is ready", re.I),
}


def group_for_domain(domain: Optional[str]) -> Optional[str]:
    d = (domain or "").lower()
    for grp, (suffixes, _, _) in GROUPS.items():
        for s in suffixes:
            if d == s or d.endswith("." + s):
                return grp
    return None


def _resolve_entrypoints() -> dict[str, Any]:
    """Import every parser module and bind its callable entry point ONCE, at
    cold start.

    A broken deploy — a missing parser module (ImportError), a missing/renamed
    entry attribute, or an entry that cannot be called with a single .eml path
    — fails HERE, at import time, so the whole invocation errors and the event
    retries / lands in the DLQ. Previously these surfaced only when a message
    happened to route to the broken group, inside the handler's per-message
    catch, where they were masked as a permanent ``parse_error`` and reported
    as success. Resolving up front turns a silent, per-message data-loss bug
    into a loud deploy failure.
    """
    resolved: dict[str, Any] = {}
    for grp, (_, module_name, entry) in GROUPS.items():
        mod = importlib.import_module(f"parsers.{module_name}")
        fn = getattr(mod, entry, None)
        if not callable(fn):
            raise ParserContractError(
                f"{grp}: parsers.{module_name}.{entry} is missing or not callable")
        try:
            inspect.signature(fn).bind("<eml-path>")
        except TypeError as exc:
            raise ParserContractError(
                f"{grp}: parsers.{module_name}.{entry} cannot be called with a "
                f"single .eml path argument: {exc}") from exc
        resolved[grp] = fn
    return resolved


# Bound once at cold start; a broken deploy fails the invocation here.
_ENTRYPOINTS: dict[str, Any] = _resolve_entrypoints()


def _validate_result(grp: str, result: Any) -> Any:
    """Assert the parser returned the shared receipt schema.

    A wrong return TYPE (e.g. a bare string) is an integration defect, not a
    per-message parse failure: without this guard it would surface downstream
    as an ``AttributeError`` on ``parsed.get(...)`` and be swallowed as
    ``parse_error``. Raise ParserContractError so it propagates to the DLQ.
    """
    if result is None or isinstance(result, dict):
        return result
    if isinstance(result, list):
        if all(isinstance(x, dict) for x in result):
            return result
        raise ParserContractError(
            f"{grp}: parser returned a list with non-dict elements")
    raise ParserContractError(
        f"{grp}: parser returned {type(result).__name__}, expected dict/list/None")


def run_parser(grp: str, eml_path: str) -> Any:
    # Entry point resolved + signature-validated at cold start; return type
    # validated here. Both raise ParserContractError (never a per-message parse
    # error) so integration defects reach the DLQ instead of masquerading as a
    # successful parse_error.
    return _validate_result(grp, _ENTRYPOINTS[grp](eml_path))


def classify(grp: str, subject: str, parsed: Any) -> str:
    """-> 'receipt' | 'txn_signal' | 'needs_ocr' | 'non_receipt'."""
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    parsed = parsed or {}
    if grp == "chase-alerts":
        # Only a recognized alert with a direction and amount is a usable
        # reconciliation signal. Marketing, security notices, and malformed or
        # spoofed mail that the parser could not classify are NOT signals.
        if (parsed.get("alert_type") and parsed.get("direction")
                and parsed.get("grand_total") is not None):
            return "txn_signal"
        return "non_receipt"
    if grp == "uber" and parsed.get("_trip_summary_no_payment"):
        # 2024+ Uber "trip summary" emails carry a fare total but state "This is
        # not a payment receipt". Honor the parser's hint over the bare total so
        # a non-charge is not emitted as a purchase.
        return "non_receipt"
    if grp == "venmo" and parsed.get("transaction_kind") in (
            "p2p_sent", "p2p_received"):
        # Peer-to-peer transfers are money movement, not merchant receipts. Only
        # a transfer with a real amount AND direction is a usable reconciliation
        # signal — subject-only matches (e.g. "You sent money on iMessage")
        # carry no amount and would emit an unusable signal, so drop them the
        # same way Chase alerts require direction + grand_total. merchant_purchase
        # falls through to the normal receipt path below.
        if (parsed.get("grand_total") is not None
                and parsed.get("direction")):
            return "txn_signal"
        return "non_receipt"
    if parsed.get("needs_pdf") or parsed.get("needs_ocr"):
        return "needs_ocr"
    gate = SUBJECT_DROP.get(grp)
    if gate and gate.search(subject or ""):
        return "non_receipt"
    if parsed.get("grand_total") is None:
        return "non_receipt"
    return "receipt"
