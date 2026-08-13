from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)


def test_parse_decimal_comma_amount():
    assert parse_receipt_amount("$8,82") == 8.82
    assert parse_receipt_amount("19,96") == 19.96


def test_parse_grouped_us_amount():
    assert parse_receipt_amount("$1,234.56") == 1234.56
    assert parse_receipt_amount("1,234") == 1234.0


def test_parse_grouped_european_amount():
    assert parse_receipt_amount("1.234,56") == 1234.56


def test_parse_negative_accounting_amount():
    assert parse_receipt_amount("($8,82)") == -8.82
    assert parse_receipt_amount("8.82-") == -8.82


def test_parse_ocr_mangled_currency_prefix():
    assert parse_receipt_amount("USD$S 7.43") == 7.43
    assert parse_receipt_amount("USD$S7.43") == 7.43
    assert parse_receipt_amount("USD$ 42.54") == 42.54
    assert parse_receipt_amount("USD$S") is None


def test_looks_like_receipt_amount_excludes_plain_integers():
    assert looks_like_receipt_amount("$8,82")
    assert looks_like_receipt_amount("8,82")
    assert not looks_like_receipt_amount("123")


def test_looks_like_receipt_amount_rejects_currency_prefix_without_digits():
    assert not looks_like_receipt_amount("USD$S")
    assert not looks_like_receipt_amount("USD$")
    assert looks_like_receipt_amount("USD$S 7.43")
    assert not looks_like_receipt_amount("USDS 7.43")
