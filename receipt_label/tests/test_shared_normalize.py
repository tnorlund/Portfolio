import types

from receipt_label.merchant_validation.normalize import (
    normalize_phone,
    normalize_address,
    normalize_url,
    build_full_address_from_words,
    build_full_address_from_lines,
)


def test_normalize_phone_basic_and_edge():
    assert normalize_phone("(805) 917-4200") == "8059174200"
    assert normalize_phone("1-805-917-4200") == "8059174200"
    assert normalize_phone("805.277.2772") == "8052772772"
    assert normalize_phone("+") == ""
    assert normalize_phone("") == ""
    # reject all identical digits
    assert normalize_phone("000-000-0000") == ""


def test_normalize_address_suffix_and_case():
    assert (
        normalize_address("1012 Westlake Blvd. Westlake, CA 91361")
        == "1012 WESTLAKE BLVD WESTLAKE CA 91361"
    )
    assert normalize_address("123 main street ste 5") == "123 MAIN ST STE 5"


def test_normalize_url_basic():
    assert (
        normalize_url("https://www.SPROUTS.com/offer?utm_source=x#frag")
        == "sprouts.com/offer"
    )
    assert (
        normalize_url("HTTP://TheStand.com/TheApp/") == "thestand.com/TheApp"
    )
    # email-like should be discarded
    assert normalize_url("info@example.com") == ""


def _mk_word(
    image_id: str,
    receipt_id: int,
    line_id: int,
    text: str,
    value: str | None = None,
    type_: str | None = None,
):
    obj = types.SimpleNamespace()
    obj.image_id = image_id
    obj.receipt_id = receipt_id
    obj.line_id = line_id
    obj.text = text
    obj.extracted_data = {}
    if type_:
        obj.extracted_data["type"] = type_
    if value is not None:
        obj.extracted_data["value"] = value
    return obj


def _mk_line(
    image_id: str,
    receipt_id: int,
    line_id: int,
    text: str,
    is_noise: bool = False,
):
    obj = types.SimpleNamespace()
    obj.image_id = image_id
    obj.receipt_id = receipt_id
    obj.line_id = line_id
    obj.text = text
    obj.is_noise = is_noise
    return obj


def test_build_full_address_from_words_and_lines():
    words = [
        _mk_word(
            "img",
            1,
            1,
            "1012 Westlake Blvd.",
            value="1012 Westlake Blvd.",
            type_="address",
        ),
        _mk_word(
            "img",
            1,
            2,
            "Westlake, CA 91361",
            value="Westlake, CA 91361",
            type_="address",
        ),
    ]
    addr = build_full_address_from_words(words)
    assert addr == "1012 WESTLAKE BLVD WESTLAKE CA 91361"

    # Fallback to lines join
    lines = [
        _mk_line("img", 1, 1, "1012 Westlake Blvd."),
        _mk_line("img", 1, 2, "Westlake, CA 91361"),
    ]
    addr2 = build_full_address_from_lines(lines)
    assert addr2 == "1012 WESTLAKE BLVD WESTLAKE CA 91361"
