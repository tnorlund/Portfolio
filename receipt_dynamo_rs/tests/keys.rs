use receipt_dynamo::keys::{self, label_gsi1_pk, pad5};
use receipt_dynamo::{keys::is_valid_uuid, Gsi4Prefix};

#[test]
fn pad5_matches_python_format() {
    assert_eq!(pad5(1), "00001");
    assert_eq!(pad5(42), "00042");
    assert_eq!(pad5(99999), "99999");
    assert_eq!(pad5(100000), "100000");
}

#[test]
fn receipt_word_sk_layout() {
    assert_eq!(
        keys::receipt_word_sk(1, 3, 4),
        "RECEIPT#00001#LINE#00003#WORD#00004"
    );
}

#[test]
fn gsi4_prefixes() {
    assert_eq!(Gsi4Prefix::Receipt.as_str(), "0_RECEIPT");
    assert_eq!(keys::gsi4_word_sk(3, 4), "3_WORD#00003#00004");
    assert_eq!(
        keys::gsi4_label_sk(3, 4, "PRODUCT_NAME"),
        "4_LABEL#00003#00004#PRODUCT_NAME"
    );
}

#[test]
fn label_gsi1_is_exactly_40_chars() {
    let pk = label_gsi1_pk("TAX");
    assert_eq!(pk.len(), 40);
    assert!(pk.starts_with("LABEL#TAX"));
    assert!(pk.ends_with('_'));
}

#[test]
fn uuid_v4_and_v5_accepted() {
    assert!(is_valid_uuid("f47ac10b-58cc-4372-a567-0e02b2c3d479"));
    assert!(is_valid_uuid("aaaaaaaa-bbbb-5ccc-8ddd-eeeeeeeeeeee"));
    assert!(!is_valid_uuid("123e4567-e89b-12d3-a456-426614174000"));
    assert!(!is_valid_uuid("not-a-uuid"));
}
