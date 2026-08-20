//! Receipt amount parsing helpers shared with Python `receipt_dynamo.amounts`.

use std::sync::LazyLock;

use regex::Regex;

const CURRENCY_SYMBOLS: &str = r"$€£¥₹";

static TOTAL_KEYWORD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(total|amount\s+due|balance|authorized)\b").unwrap());
static SUBTOTAL_KEYWORD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bsub[-\s]?total\b").unwrap());
static TAX_KEYWORD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(tax|vat)\b").unwrap());
static NON_PAYMENT_SUMMARY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(savings?|discounts?|refunds?|returns?|coupons?|promos?|promotion|rewards?|loyalty|cash\s+back|cashback|store\s+credit)\b",
    )
    .unwrap()
});
static TENDER_KEYWORD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(tender(?:ed)?|cash|change)\b").unwrap());
static DECIMAL_AMOUNT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"-?\(?\d+([.,]\d{2})\)?-?").unwrap());
static GROUPED_AMOUNT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"-?\(?\d{1,3}(,\d{3})+(\.\d{2})?\)?-?").unwrap());
static HAS_DIGIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d").unwrap());
static CURRENCY_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!("[{}]", regex::escape(CURRENCY_SYMBOLS))).unwrap());
static STRIP_CURRENCY_WS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"[{}\s]", regex::escape(CURRENCY_SYMBOLS))).unwrap());
static NON_NUMERIC: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^0-9,.-]").unwrap());
static TOKEN_SPLIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z]+").unwrap());
static EURO_GROUPED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\d{1,3}(\.\d{3})+,\d{2}$").unwrap());
static THOUSANDS_ONLY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\d{1,3}(,\d{3})+$").unwrap());
static DECIMAL_COMMA: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d+,\d{2}$").unwrap());

const GRAND_TOTAL_DISQUALIFIER_TOKENS: &[&str] = &[
    "number",
    "items",
    "item",
    "qty",
    "quantity",
    "count",
    "sold",
    "transactions",
    "transaction",
    "pieces",
    "units",
    "lines",
    "savings",
    "saved",
];

/// Return whether joined line text reads as a grand-total row.
pub fn is_grand_total_line(line_text: &str) -> bool {
    if line_text.is_empty() {
        return false;
    }
    if !TOTAL_KEYWORD_RE.is_match(line_text) {
        return false;
    }
    if SUBTOTAL_KEYWORD_RE.is_match(line_text) {
        return false;
    }
    if TAX_KEYWORD_RE.is_match(line_text) {
        return false;
    }
    if NON_PAYMENT_SUMMARY_RE.is_match(line_text) {
        return false;
    }
    let lower = line_text.to_ascii_lowercase();
    !TOKEN_SPLIT
        .split(&lower)
        .filter(|t| !t.is_empty())
        .any(|token| GRAND_TOTAL_DISQUALIFIER_TOKENS.contains(&token))
}

/// Parse receipt currency/amount text without changing the OCR text.
pub fn parse_receipt_amount(text: &str) -> Option<f64> {
    let raw = text.trim();
    if raw.is_empty() {
        return None;
    }

    let mut is_negative = raw.starts_with('(') && raw.ends_with(')');
    let mut cleaned = STRIP_CURRENCY_WS.replace_all(raw, "").into_owned();

    if cleaned.starts_with('(') && cleaned.ends_with(')') {
        cleaned = cleaned[1..cleaned.len() - 1].to_string();
    }

    if cleaned.ends_with('-') {
        is_negative = true;
        cleaned.pop();
    }

    if cleaned.starts_with('-') {
        is_negative = true;
        cleaned.remove(0);
    }

    cleaned = NON_NUMERIC.replace_all(&cleaned, "").into_owned();
    if cleaned.is_empty() || !HAS_DIGIT.is_match(&cleaned) {
        return None;
    }

    let normalized = normalize_decimal_separators(&cleaned)?;
    let value: f64 = normalized.parse().ok()?;
    Some(if is_negative { -value } else { value })
}

/// Return whether text has receipt amount punctuation/symbol context.
pub fn looks_like_receipt_amount(text: &str) -> bool {
    let raw = text.trim();
    if raw.is_empty() || !HAS_DIGIT.is_match(raw) {
        return false;
    }
    CURRENCY_RE.is_match(raw)
        || full_match(&GROUPED_AMOUNT_RE, raw)
        || full_match(&DECIMAL_AMOUNT_RE, raw)
}

fn full_match(re: &Regex, text: &str) -> bool {
    re.find(text)
        .is_some_and(|m| m.start() == 0 && m.end() == text.len())
}

fn normalize_decimal_separators(cleaned: &str) -> Option<String> {
    if !cleaned.contains(',') {
        return Some(cleaned.to_string());
    }

    let last_comma = cleaned.rfind(',')?;
    let last_dot = cleaned.rfind('.');

    if let Some(last_dot) = last_dot {
        if last_comma > last_dot {
            if EURO_GROUPED.is_match(cleaned) {
                return Some(cleaned.replace('.', "").replace(',', "."));
            }
            return Some(cleaned.replace('.', "").replace(',', "."));
        }
        return Some(cleaned.replace(',', ""));
    }

    if THOUSANDS_ONLY.is_match(cleaned) {
        return Some(cleaned.replace(',', ""));
    }
    if DECIMAL_COMMA.is_match(cleaned) {
        return Some(cleaned.replace(',', "."));
    }
    Some(cleaned.replace(',', ""))
}

/// Exposed for tests that assert the Python-mirrored tender regex still compiles.
pub fn has_tender_keyword(text: &str) -> bool {
    TENDER_KEYWORD_RE.is_match(text)
}
