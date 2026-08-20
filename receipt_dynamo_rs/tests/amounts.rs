use receipt_dynamo::{is_grand_total_line, looks_like_receipt_amount, parse_receipt_amount};

#[test]
fn parse_decimal_comma_amount() {
    assert_eq!(parse_receipt_amount("$8,82"), Some(8.82));
    assert_eq!(parse_receipt_amount("19,96"), Some(19.96));
}

#[test]
fn parse_grouped_us_amount() {
    assert_eq!(parse_receipt_amount("$1,234.56"), Some(1234.56));
    assert_eq!(parse_receipt_amount("1,234"), Some(1234.0));
}

#[test]
fn parse_grouped_european_amount() {
    assert_eq!(parse_receipt_amount("1.234,56"), Some(1234.56));
}

#[test]
fn parse_negative_accounting_amount() {
    assert_eq!(parse_receipt_amount("($8,82)"), Some(-8.82));
    assert_eq!(parse_receipt_amount("8.82-"), Some(-8.82));
}

#[test]
fn parse_ocr_mangled_currency_prefix() {
    assert_eq!(parse_receipt_amount("USD$S 7.43"), Some(7.43));
    assert_eq!(parse_receipt_amount("USD$S7.43"), Some(7.43));
    assert_eq!(parse_receipt_amount("USD$ 42.54"), Some(42.54));
    assert_eq!(parse_receipt_amount("USD$S"), None);
}

#[test]
fn looks_like_receipt_amount_excludes_plain_integers() {
    assert!(looks_like_receipt_amount("$8,82"));
    assert!(looks_like_receipt_amount("8,82"));
    assert!(!looks_like_receipt_amount("123"));
}

#[test]
fn looks_like_receipt_amount_rejects_currency_prefix_without_digits() {
    assert!(!looks_like_receipt_amount("USD$S"));
    assert!(!looks_like_receipt_amount("USD$"));
    assert!(looks_like_receipt_amount("USD$S 7.43"));
    assert!(!looks_like_receipt_amount("USDS 7.43"));
}

#[test]
fn grand_total_heuristics() {
    assert!(is_grand_total_line("Total 21.45"));
    assert!(!is_grand_total_line("Subtotal 18.00"));
    assert!(!is_grand_total_line("Tax 1.45"));
    assert!(!is_grand_total_line("TOTAL NUMBER OF ITEMS SOLD 12"));
    assert!(!is_grand_total_line(""));
}
