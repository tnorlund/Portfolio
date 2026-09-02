"""PROVEN policy helper: exact-to-the-cent on both truth-chain hops.

Policy constant (user-decided 2026-08-03): PROVEN requires hop 1
(items -> printed) to reconcile as a full ``match`` — ``near`` never
counts — and hop 2 (printed total -> bank amount) to agree within
$0.005. Anything else, including the tolerance-band shapes that
produced the pilot's false accept, is not proven.
"""

import pytest
from receipt_upload.line_items.geometry import (
    PROVEN_CENT_TOLERANCE,
    is_proven,
)


def test_exact_both_hops_is_proven():
    assert is_proven("match", 21.07, 21.07) is True


def test_sub_half_cent_float_noise_is_proven():
    # Float noise below half a cent must not cost a proof.
    assert is_proven("match", 21.07, 21.07 + 0.004) is True


def test_half_cent_or_more_is_not_proven():
    assert is_proven("match", 21.07, 21.075) is False
    assert is_proven("match", 21.07, 21.08) is False


def test_near_never_proven_even_when_bank_exact():
    # "near" never counts, however exact the bank hop is.
    assert is_proven("near", 21.07, 21.07) is False


@pytest.mark.parametrize(
    "status", ["mismatch", "no-baseline", None, "", "MATCH"]
)
def test_non_match_statuses_never_proven(status):
    assert is_proven(status, 10.00, 10.00) is False


def test_pilot_false_accept_shape_0_97_vs_1_07():
    """The 1828b9ba shape: a dime hides inside the tolerance bands.

    Printed tax was 0.97 but the stored figure read 1.07, so the
    stored printed-total sits $0.10 above what the bank charged. The
    1%-of-baseline 'match' band on a ~$21 receipt is $0.21, so a
    ten-cent error sails straight through every band-tolerant check —
    which is exactly how the truth chain false-accepted it and only
    the image caught it. is_proven must reject it: hop 2 is off by
    ten full cents.
    """
    stored_printed_total = 21.07  # OCR/stored figure, tax read as 1.07
    bank_amount = 20.97  # what the card was actually charged
    assert is_proven("match", stored_printed_total, bank_amount) is False
    # And the band that absorbed it ("near") never proves anything.
    assert is_proven("near", stored_printed_total, bank_amount) is False


@pytest.mark.parametrize(
    "printed,bank",
    [(None, 10.00), (10.00, None), (None, None), ("n/a", 10.00)],
)
def test_missing_or_bad_figures_fail_closed(printed, bank):
    assert is_proven("match", printed, bank) is False


def test_string_numerics_are_coerced():
    assert is_proven("match", "21.07", "21.07") is True


def test_tolerance_constant_is_half_a_cent():
    assert PROVEN_CENT_TOLERANCE == 0.005
