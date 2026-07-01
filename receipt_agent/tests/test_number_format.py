"""PR-4 guard: the US NumberFormat must reproduce every former hardcoded pattern
byte-for-byte, so migrating the sites to the factory can't change behavior."""
from receipt_agent.agents.label_evaluator.rendering.number_format import (
    US,
    date_core,
    fraction,
    integer_part,
)

CUR = US.currency
DEC = US.decimal
FLAG = US.tax_flag


def test_date_patterns_match_former_literals():
    assert f"^{date_core(US)}$" == r"^\d{1,2}/\d{1,2}/\d{2,4}$"          # _DATE_LED
    assert (
        f"^{date_core(US)}$|^\\d{{8,}}$" == r"^\d{1,2}/\d{1,2}/\d{2,4}$|^\d{8,}$"
    )  # _DATE_TOKEN
    assert (
        f"^{date_core(US, groups=True)}$" == r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$"
    )  # _DATE_MDY


def test_price_patterns_match_former_literals():
    # font_profile._PRICE_TOKEN
    assert (
        f"^{CUR}?{integer_part(US)}(?:{fraction(US)}){CUR}?{FLAG}?$"
        == r"^\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})\$?[A-Z]?$"
    )
    # receipt_grid._PRICE_TOKEN
    assert (
        f"^[-+]?{CUR}?{integer_part(US, allow_bare=True)}{fraction(US)}[-+]?{FLAG}?$"
        == r"^[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}[-+]?[A-Z]?$"
    )
    # content_clean._PRICE_TRAILING_DOT
    assert (
        f"^({CUR}?{integer_part(US)}{fraction(US)})\\.$"
        == r"^(\$?\d{1,3}(?:,\d{3})*\.\d{2})\.$"
    )
    # content_clean._AMOUNT_3DEC (three-decimal error detector: fraction is \d{3})
    assert (
        f"^([-+]?{CUR}?{integer_part(US)}){DEC}(\\d{{3}})([-+]?{FLAG}?)$"
        == r"^([-+]?\$?\d{1,3}(?:,\d{3})*)\.(\d{3})([-+]?[A-Z]?)$"
    )
