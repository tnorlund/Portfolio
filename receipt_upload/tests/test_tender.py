"""Unit tests for receipt_upload.tender.

Payment-zone text samples are real OCR line texts pulled from dev
receipts (joined_rows.json / lines.json from the 2026-07 tender
report), covering each classifier decision and both documented
false-positive sources: footer cash boilerplate and the OCR-flattened
CHANGE/AMOUNT column split.
"""

from receipt_upload.tender import (
    TenderClassification,
    classify_tender,
    classify_tender_for_receipt,
    payment_zone_texts,
)

# Real Sprouts payment zone: Mastercard contactless with masked PAN.
SPROUTS_MASTERCARD = [
    "MASTERCARD",
    "CARD #:",
    "PURCHASE",
    "AUTH CODE: 70626Z",
    "Entry Method: Cntctless",
    "XXXXXXXXXXXX5061",
    "- APPROVED",
    "Mode:",
    "Issuer",
]

# Real Sprouts footer boilerplate that poisons a naive \bCASH\b scan.
SPROUTS_FOOTER = [
    "Qualifying purchase calculated after applying all",
    "other coupons and discounts and excludes",
    "amounts for tax, alcohol, postage & gift cards.",
    "Maximum discount $10. Not valid online or on",
    "prior purchases. To use in-store scan receipt",
    "barcode. Limit one per transaction. Cannot be",
    "combined with other Sprouts cash-off offers or",
    "employee discount. No cash redemption unless",
    "required by law @ 1/100c. Void if duplicated,",
]

# Real cash tender: CASH heads a tender line, CHANGE amount same-line.
CASH_ZONE = [
    "CASH 25.00",
    "CHANGE 1.78",
    "Bring this back anytime from May 27-June 2*",
    "No cash redemption unless",
    "required by law @ 1/100c.",
]

# Real chiropractor terminal slip: VISA chip sale, debit marker.
VISA_DEBIT_ZONE = [
    "SALE",
    "04/25/25",
    "APPR CODE: 063015",
    "VISA",
    "US DEBIT",
    "Chip",
    "TVR: 80 80 00 80 00",
    "CUSTOMER COPY",
]

# Real generic-card slip: auth text but no network or last4.
GENERIC_CARD_ZONE = [
    "WITHDRAWAL FROM CHECKING",
    "CARD NUMBER:",
    "H0663",
    "AUTH",
    "#:",
    "962400005565",
]


class TestNetwork:
    def test_mastercard(self):
        result = classify_tender(SPROUTS_MASTERCARD)
        assert result.card_network == "MASTERCARD"
        assert result.tender_class == "card"
        assert result.tender_detail == "card"

    def test_visa(self):
        result = classify_tender(VISA_DEBIT_ZONE)
        assert result.card_network == "VISA"

    def test_amex_variants(self):
        assert (
            classify_tender(["AMERICAN EXPRESS ***********1005"]).card_network
            == "AMEX"
        )
        assert classify_tender(["AMEX ENDING IN 6081"]).card_network == "AMEX"

    def test_discover(self):
        assert classify_tender(["DISCOVER"]).card_network == "DISCOVER"

    def test_bare_mc_needs_card_context(self):
        # inside a merchant-ish token: not a network
        assert classify_tender(["MCDONALDS"]).card_network is None
        # bare MC alone, no card evidence: still not a network
        assert classify_tender(["MC"]).card_network is None
        # bare MC + last4: Mastercard
        result = classify_tender(["MC ************7645"])
        assert result.card_network == "MASTERCARD"


class TestLast4:
    def test_masked_pan(self):
        assert classify_tender(SPROUTS_MASTERCARD).card_last4 == "5061"

    def test_masked_pan_variants(self):
        assert classify_tender(["************1454"]).card_last4 == "1454"
        assert classify_tender(["#### #### #### 3931"]).card_last4 == "3931"
        assert classify_tender(["•••• 0663"]).card_last4 == "0663"

    def test_ending_in(self):
        assert classify_tender(["CARD ENDING IN 5894"]).card_last4 == "5894"

    def test_card_number_prefix(self):
        assert classify_tender(["CARD #: 1769"]).card_last4 == "1769"

    def test_no_last4(self):
        assert classify_tender(VISA_DEBIT_ZONE).card_last4 is None


class TestDebitCredit:
    def test_us_debit(self):
        assert classify_tender(VISA_DEBIT_ZONE).card_kind == "debit"

    def test_credit(self):
        result = classify_tender(["VISA CREDIT", "APPROVED"])
        assert result.card_kind == "credit"

    def test_debit_beats_credit(self):
        result = classify_tender(["US DEBIT", "CREDIT"])
        assert result.card_kind == "debit"

    def test_unstated(self):
        assert classify_tender(SPROUTS_MASTERCARD).card_kind is None


class TestCash:
    def test_cash_tender_line(self):
        result = classify_tender(CASH_ZONE)
        assert result.tender_class == "cash"
        assert result.tender_detail == "cash"
        assert result.card_network is None

    def test_footer_boilerplate_is_not_cash(self):
        result = classify_tender(SPROUTS_FOOTER)
        assert result.tender_class == "unknown"
        assert result.tender_detail == "unknown"

    def test_change_same_line_nonzero_is_cash(self):
        result = classify_tender(["TOTAL 6.22", "CHANGE 3.78"])
        assert result.tender_class == "cash"

    def test_change_next_line_amount_is_not_cash(self):
        # OCR flattens tender columns: the 93.41 on the next line is
        # the card's own AMOUNT, not change.
        result = classify_tender(["CHANGE", "93.41", "VISA ************1454"])
        assert result.tender_class == "card"
        assert result.tender_detail == "card"

    def test_change_zero_is_not_cash(self):
        result = classify_tender(["CHANGE 0.00"])
        assert result.tender_class == "unknown"

    def test_cashier_is_not_cash(self):
        assert classify_tender(["CASHIER: ALEX 12.00"]).tender_class == (
            "unknown"
        )

    def test_card_plus_cash_token_is_split_but_card_class(self):
        zone = SPROUTS_MASTERCARD + ["CASH 20.00"]
        result = classify_tender(zone)
        assert result.tender_detail == "split_or_ambiguous"
        assert result.tender_class == "card"
        assert result.card_last4 == "5061"

    def test_labeled_tender_word_cash(self):
        result = classify_tender(
            ["THANK YOU"], labeled_words=[("PAYMENT_METHOD", "CASH")]
        )
        assert result.tender_class == "cash"

    def test_labeled_change_nonzero(self):
        result = classify_tender(
            ["THANK YOU"], labeled_words=[("CHANGE", "4.22")]
        )
        assert result.tender_class == "cash"


class TestGenericCard:
    def test_auth_text_without_network(self):
        result = classify_tender(GENERIC_CARD_ZONE)
        assert result.tender_detail == "card_generic"
        assert result.tender_class == "card"
        assert result.card_network is None
        assert result.card_last4 is None

    def test_apple_pay_is_card_evidence(self):
        result = classify_tender(["APPLE PAY", "TOTAL 12.00"])
        assert result.tender_detail == "card"


class TestUnknown:
    def test_empty_zone(self):
        result = classify_tender([])
        assert result == TenderClassification(
            tender_class="unknown", tender_detail="unknown"
        )

    def test_plain_footer(self):
        result = classify_tender(["THANK YOU", "PLEASE COME AGAIN"])
        assert result.tender_class == "unknown"


class TestPaymentZone:
    def _lines(self):
        return [
            {"line_id": 1, "text": "SPROUTS", "y": 0.95},
            {"line_id": 2, "text": "TOTAL 47.18", "y": 0.50},
            {"line_id": 3, "text": "MASTERCARD", "y": 0.40},
            {"line_id": 4, "text": "XXXXXXXXXXXX7645", "y": 0.35},
            {"line_id": 5, "text": "THANK YOU", "y": 0.10},
        ]

    def test_payment_section_wins(self):
        sections = [
            {"section_type": "PAYMENT", "line_ids": [3, 4]},
            {"section_type": "FOOTER", "line_ids": [5]},
        ]
        texts, has_payment = payment_zone_texts(self._lines(), sections)
        assert has_payment is True
        assert texts == ["MASTERCARD", "XXXXXXXXXXXX7645", "THANK YOU"]

    def test_below_total_line_fallback(self):
        sections = [{"section_type": "TOTAL_LINE", "line_ids": [2]}]
        texts, has_payment = payment_zone_texts(self._lines(), sections)
        assert has_payment is False
        # everything at or below the TOTAL_LINE's y (bottom-origin)
        assert texts == [
            "TOTAL 47.18",
            "MASTERCARD",
            "XXXXXXXXXXXX7645",
            "THANK YOU",
        ]

    def test_bottom_40pct_fallback(self):
        texts, has_payment = payment_zone_texts(self._lines(), [])
        assert has_payment is False
        assert texts == ["MASTERCARD", "XXXXXXXXXXXX7645", "THANK YOU"]

    def test_entity_style_lines(self):
        class Line:
            def __init__(self, line_id, text, y):
                self.line_id = line_id
                self.text = text
                self.bounding_box = {"x": 0.1, "y": y}

        class Section:
            def __init__(self, section_type, line_ids):
                self.section_type = section_type
                self.line_ids = line_ids

        lines = [Line(1, "VISA US DEBIT", 0.2), Line(2, "HEADER", 0.9)]
        sections = [Section("PAYMENT", [1])]
        texts, has_payment = payment_zone_texts(lines, sections)
        assert texts == ["VISA US DEBIT"]
        assert has_payment is True


class TestClassifyForReceipt:
    def test_end_to_end_with_labels(self):
        lines = [
            {"line_id": 1, "text": "TOTAL 25.00", "y": 0.5},
            {"line_id": 2, "text": "THANK YOU", "y": 0.1},
        ]
        sections = [{"section_type": "PAYMENT", "line_ids": [2]}]
        word_labels = [
            {"label": "PAYMENT_METHOD", "line_id": 1, "word_id": 3},
            {"label": "GRAND_TOTAL", "line_id": 1, "word_id": 2},
        ]
        words = [
            {"line_id": 1, "word_id": 3, "text": "CASH"},
            {"line_id": 1, "word_id": 2, "text": "25.00"},
        ]
        result = classify_tender_for_receipt(
            lines, sections, word_labels, words
        )
        assert result.tender_class == "cash"

    def test_end_to_end_card(self):
        lines = [
            {"line_id": 1, "text": "VISA US DEBIT", "y": 0.3},
            {"line_id": 2, "text": "************1454", "y": 0.25},
        ]
        sections = [{"section_type": "PAYMENT", "line_ids": [1, 2]}]
        result = classify_tender_for_receipt(lines, sections)
        assert result.tender_class == "card"
        assert result.card_network == "VISA"
        assert result.card_last4 == "1454"
        assert result.card_kind == "debit"
