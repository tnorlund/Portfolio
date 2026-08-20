//! Canonical receipt label vocabulary matching `receipt_dynamo.constants`.

use std::collections::HashMap;
use std::sync::LazyLock;

use crate::error::{Error, Result};

/// Core receipt label types with descriptions.
pub static CORE_LABELS: LazyLock<HashMap<&'static str, &'static str>> = LazyLock::new(|| {
    HashMap::from([
        (
            "MERCHANT_NAME",
            "Trading name or brand of the store issuing the receipt.",
        ),
        (
            "STORE_HOURS",
            "Printed business hours or opening times for the merchant.",
        ),
        (
            "PHONE_NUMBER",
            "Telephone number printed on the receipt (store's main line).",
        ),
        (
            "WEBSITE",
            "Web or email address printed on the receipt (e.g., sprouts.com).",
        ),
        (
            "LOYALTY_ID",
            "Customer loyalty / rewards / membership identifier.",
        ),
        (
            "ADDRESS_LINE",
            "Full address line (street + city etc.) printed on the receipt.",
        ),
        ("DATE", "Calendar date of the transaction."),
        ("TIME", "Time of the transaction."),
        (
            "PAYMENT_METHOD",
            "Payment instrument summary (e.g., VISA ••••1234, CASH).",
        ),
        ("COUPON", "Coupon code or description that reduces price."),
        (
            "DISCOUNT",
            "Any non-coupon discount line item (e.g., 10% member discount).",
        ),
        (
            "PRODUCT_NAME",
            "Descriptive text of a purchased product (item name).",
        ),
        (
            "QUANTITY",
            "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
        ),
        ("UNIT_PRICE", "Price per single unit / weight before tax."),
        (
            "LINE_TOTAL",
            "Extended price for that line (quantity x unit price).",
        ),
        (
            "SUBTOTAL",
            "Sum of all line totals before tax and discounts.",
        ),
        ("TAX", "Any tax line (sales tax, VAT, bottle deposit)."),
        ("TIP", "Gratuity or tip amount added by customer."),
        (
            "GRAND_TOTAL",
            "Final amount due after all discounts, taxes and fees.",
        ),
        (
            "CHANGE",
            "Change amount returned to the customer after transaction.",
        ),
        ("CASH_BACK", "Cash back amount dispensed from purchase."),
        ("REFUND", "Refund amount (full or partial return)."),
    ])
});

pub static CORE_LABEL_NAMES: LazyLock<Vec<&'static str>> = LazyLock::new(|| {
    let mut names: Vec<_> = CORE_LABELS.keys().copied().collect();
    names.sort_unstable();
    names
});

pub static NON_CORE_LABEL_ALIASES: LazyLock<HashMap<&'static str, &'static str>> =
    LazyLock::new(|| {
        HashMap::from([
            ("ADDRESS", "ADDRESS_LINE"),
            ("BUSINESS_NAME", "MERCHANT_NAME"),
            ("CARD_NUMBER", "PAYMENT_METHOD"),
            ("PAYMENT_TYPE", "PAYMENT_METHOD"),
        ])
    });

pub fn canonical_label_name(label: &str) -> String {
    label.trim().to_ascii_uppercase()
}

pub fn is_core_label(label: &str) -> bool {
    CORE_LABELS.contains_key(canonical_label_name(label).as_str())
}

pub fn normalize_label_alias(label: &str) -> Option<String> {
    let canonical = canonical_label_name(label);
    if CORE_LABELS.contains_key(canonical.as_str()) {
        return Some(canonical);
    }
    NON_CORE_LABEL_ALIASES
        .get(canonical.as_str())
        .map(|s| (*s).to_string())
}

pub fn invalid_label_message(label: &str) -> String {
    let canonical = canonical_label_name(label);
    let mut message = format!(
        "Invalid label {canonical:?}: label must be one of {:?}",
        *CORE_LABEL_NAMES
    );
    if let Some(suggestion) = NON_CORE_LABEL_ALIASES.get(canonical.as_str()) {
        message.push_str(&format!(". Did you mean {suggestion:?}?"));
    }
    message
}

/// Authoring guard: only mint new label sort keys through this function.
pub fn normalize_core_label(label: &str) -> Result<String> {
    normalize_label_alias(label).ok_or_else(|| Error::validation(invalid_label_message(label)))
}
