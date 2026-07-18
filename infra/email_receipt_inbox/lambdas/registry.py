"""Sender-domain -> parser registry for inbound email receipts.

Parsers are stdlib-only modules exposing ``parse(path) -> dict`` (or
``parse_eml``) that take an RFC-822 .eml file path and return the shared
receipt schema (dollars as floats, ``grand_total is None`` for non-receipts).
"""
from __future__ import annotations

import importlib
import re
from typing import Any, Optional

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


def run_parser(grp: str, eml_path: str) -> Any:
    _, module_name, entry = GROUPS[grp]
    mod = importlib.import_module(f"parsers.{module_name}")
    return getattr(mod, entry)(eml_path)


def classify(grp: str, subject: str, parsed: Any) -> str:
    """-> 'receipt' | 'txn_signal' | 'needs_ocr' | 'non_receipt'."""
    if grp == "chase-alerts":
        return "txn_signal"
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    parsed = parsed or {}
    if parsed.get("needs_pdf") or parsed.get("needs_ocr"):
        return "needs_ocr"
    gate = SUBJECT_DROP.get(grp)
    if gate and gate.search(subject or ""):
        return "non_receipt"
    if parsed.get("grand_total") is None:
        return "non_receipt"
    return "receipt"
