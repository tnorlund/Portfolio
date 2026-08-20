use receipt_dynamo::{
    canonical_label_name, invalid_label_message, is_core_label, normalize_core_label,
    normalize_label_alias, CORE_LABEL_NAMES,
};

#[test]
fn core_label_count_matches_python() {
    assert_eq!(CORE_LABEL_NAMES.len(), 22);
    assert!(is_core_label("grand_total"));
    assert_eq!(canonical_label_name("  tax "), "TAX");
}

#[test]
fn aliases_rewrite_safely() {
    assert_eq!(
        normalize_label_alias("ADDRESS").as_deref(),
        Some("ADDRESS_LINE")
    );
    assert_eq!(
        normalize_core_label("business_name").unwrap(),
        "MERCHANT_NAME"
    );
    assert!(normalize_core_label("NOT_A_LABEL").is_err());
    assert!(invalid_label_message("ADDRESS").contains("ADDRESS_LINE"));
}
