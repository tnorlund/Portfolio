"""Reject a Google Places ``displayName`` that is really a street address.

Google returns street-address / premise entries whose ``displayName`` is
the address itself. Observed on freshly-ingested receipts: place
``ChIJO1cwiRDQyIAR5C3eBgKmGcw`` (the Trader Joe's at 2716 N Green Valley
Pkwy, Henderson NV) displays as "2716 N Green Valley Pkwy", and that
string was stored as ``ReceiptPlace.merchant_name`` -> ``ReceiptSummary
.merchant_name`` -> the ``MERCHANT#`` GSI1 partition keys, so the receipt
grouped under its own address instead of under Trader Joe's.
"""

import pytest
from receipt_upload.merchant_resolution.resolver import (
    looks_like_street_address,
    place_name_is_address_derived,
    prefer_receipt_name_over_address,
    sanitize_receipt_merchant_name,
)

pytestmark = pytest.mark.unit


class TestLooksLikeStreetAddress:
    @pytest.mark.parametrize(
        "name",
        [
            "2716 N Green Valley Pkwy",
            "2300 Paseo Verde Pkwy",
            "614 Gravier St",
            "101 N Westlake Blvd",
            "1234 South Main Street",
            "500 Sunset Blvd.",
        ],
    )
    def test_flags_bare_street_addresses(self, name: str) -> None:
        assert looks_like_street_address(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "Trader Joe's",
            "Sprouts Farmers Market",
            "Costco Wholesale",
            "In-N-Out Burger",
            "7-Eleven",
            # Numeric leading token but no street type -- a real pub name.
            "14 Cannons",
            # Street type embedded but the name continues past it.
            "9255 Sunset Blvd. Garage (Imperial Parking)",
            "99 Ranch Market",
            "",
            None,
        ],
    )
    def test_does_not_flag_business_names(self, name) -> None:
        assert looks_like_street_address(name) is False


class TestPlaceNameIsAddressDerived:
    def test_requires_both_signals(self) -> None:
        assert (
            place_name_is_address_derived(
                "2716 N Green Valley Pkwy",
                "2716 N Green Valley Pkwy, Henderson, NV 89014, USA",
            )
            is True
        )

    def test_address_shaped_name_that_is_not_this_places_address(self) -> None:
        """A street-address-shaped name is left alone if it does not
        prefix the place's own formatted address -- that would be a
        different (and unexplained) mismatch, not this defect."""
        assert (
            place_name_is_address_derived(
                "2716 N Green Valley Pkwy",
                "17 Elm Street, Boston, MA 02108, USA",
            )
            is False
        )

    def test_business_name_never_flagged(self) -> None:
        assert (
            place_name_is_address_derived(
                "Trader Joe's",
                "2716 N Green Valley Pkwy, Henderson, NV 89014, USA",
            )
            is False
        )

    def test_missing_address_is_not_enough(self) -> None:
        assert (
            place_name_is_address_derived("2716 N Green Valley Pkwy", None)
            is False
        )


class TestSanitizeReceiptMerchantName:
    """The OCR-derived candidate is normalised, or rejected outright."""

    def test_collapses_a_doubled_phrase(self) -> None:
        """IMG_3404 labels TRADER JOE'S in both header and footer, so the
        joined labeled text is the brand twice. Uncollapsed that yields
        the GSI key MERCHANT#TRADER_JOE_S_TRADER_JOE_S."""
        assert (
            sanitize_receipt_merchant_name("TRADER JOE'S TRADER JOE'S")
            == "TRADER JOE'S"
        )

    def test_collapses_a_tripled_phrase(self) -> None:
        assert sanitize_receipt_merchant_name("VONS VONS VONS") == "VONS"

    def test_keeps_a_name_that_merely_repeats_one_token(self) -> None:
        assert (
            sanitize_receipt_merchant_name("Wild Fork Fork Market")
            == "Wild Fork Fork Market"
        )

    def test_rejects_a_footer_sentence(self) -> None:
        """The Regal receipt's topmost line by y-centroid: these receipts
        are bottom-origin so the geometric 'first line' is the footer."""
        assert (
            sanitize_receipt_merchant_name(
                "purchases of gift cards or alcohol. Restrictions apply."
            )
            is None
        )

    def test_rejects_a_website(self) -> None:
        assert sanitize_receipt_merchant_name("www.traderjoes.com") is None

    def test_rejects_a_brand_prefixed_address_echo(self) -> None:
        """'WF 101 Westlake Blvd.' is not address-shaped (starts with a
        letter) but is still the address, so substituting buys nothing."""
        assert (
            sanitize_receipt_merchant_name(
                "WF 101 Westlake Blvd.",
                "101 N Westlake Blvd, Thousand Oaks, CA 91362, USA",
            )
            is None
        )

    def test_keeps_a_business_name_containing_a_street_word(self) -> None:
        """No shared number with the place address -> not an echo."""
        assert (
            sanitize_receipt_merchant_name(
                "614 Gravier LLC",
                "614 Gravier St, New Orleans, LA 70130, USA",
            )
            == "614 Gravier LLC"
        )

    @pytest.mark.parametrize("name", ["", None, "TJ"])
    def test_rejects_empty_or_too_short(self, name) -> None:
        assert sanitize_receipt_merchant_name(name) is None


class TestPreferReceiptNameOverAddress:
    ADDR = "2716 N Green Valley Pkwy"
    FULL = "2716 N Green Valley Pkwy, Henderson, NV 89014, USA"

    def test_substitutes_the_receipts_own_merchant_name(self) -> None:
        assert (
            prefer_receipt_name_over_address(
                self.ADDR, self.FULL, "TRADER JOE'S"
            )
            == "TRADER JOE'S"
        )

    def test_substitutes_the_collapsed_doubled_name(self) -> None:
        """End-to-end for IMG_3404 / IMG_3420."""
        assert (
            prefer_receipt_name_over_address(
                self.ADDR, self.FULL, "TRADER JOE'S TRADER JOE'S"
            )
            == "TRADER JOE'S"
        )

    def test_declines_when_the_receipt_offers_only_a_footer_sentence(
        self,
    ) -> None:
        """IMG_3411 (Regal) has no MERCHANT_NAME label; the guard must be
        a no-op rather than storing junk."""
        assert (
            prefer_receipt_name_over_address(
                "2300 Paseo Verde Pkwy",
                "2300 Paseo Verde Pkwy, Henderson, NV 89052, USA",
                "purchases of gift cards or alcohol. Restrictions apply.",
            )
            == "2300 Paseo Verde Pkwy"
        )

    def test_keeps_places_name_when_it_is_a_business_name(self) -> None:
        assert (
            prefer_receipt_name_over_address(
                "Trader Joe's", self.FULL, "SOMETHING ELSE"
            )
            == "Trader Joe's"
        )

    def test_never_blanks_the_name_when_no_receipt_name(self) -> None:
        """Falling back to the address beats storing None: ReceiptPlace
        requires a non-empty merchant_name."""
        assert (
            prefer_receipt_name_over_address(self.ADDR, self.FULL, None)
            == self.ADDR
        )
        assert (
            prefer_receipt_name_over_address(self.ADDR, self.FULL, "  ")
            == self.ADDR
        )

    def test_rejects_a_receipt_name_that_is_also_an_address(self) -> None:
        assert (
            prefer_receipt_name_over_address(
                self.ADDR, self.FULL, "2716 North Green Valley Parkway"
            )
            == self.ADDR
        )

    def test_rejects_a_too_short_receipt_name(self) -> None:
        assert (
            prefer_receipt_name_over_address(self.ADDR, self.FULL, "TJ")
            == self.ADDR
        )
