"""The price-token regex, defined once (P1b of the render refactor, #1188).

Five modules each compiled their own "is this token a money amount" pattern:

* ``receipt_grid.py``     -- grid right-snap decision (signs, bare integers,
  tax flag): the FULL-fidelity form.
* ``font_profile.py``     -- price-column measurement (no signs, no bare
  integer part, optional trailing currency).
* ``render_synthetic_receipts.py`` -- cached-line-example detection (signs,
  no tax flag, no bare integer part).
* ``compose_dollartree.py`` -- shattered-photo-OCR fragments (dotless bodies,
  ``T`` flag only).
* ``glyph_renderer.py``   -- right-aligned stamping (4-digit head, optional
  trailing currency/flag/minus).

The five patterns accept genuinely DIFFERENT token languages, and each
difference is load-bearing at its call site (the grid pattern decides which
edges snap; loosening/tightening any of them changes rendered bytes). So the
dedupe keeps every historical pattern byte-for-byte identical -- each is
declared here ONCE and imported by its call site -- while new measurement
code (full_fidelity_eval, stylescan columns) uses the canonical
:data:`PRICE_TOKEN` / :func:`is_price_token`.

``tests/test_price_tokens.py`` pins each pattern string so a drive-by "just
unify them" edit fails loudly instead of silently reflowing renders.
"""

from __future__ import annotations

import re

from receipt_agent.agents.label_evaluator.rendering.number_format import (
    US as _NF,
)
from receipt_agent.agents.label_evaluator.rendering.number_format import (
    fraction as _fraction,
)
from receipt_agent.agents.label_evaluator.rendering.number_format import (
    integer_part as _integer_part,
)

# --- canonical pattern (new measurement code uses THIS one) ----------------
#
# A right-aligned amount token: optional sign/currency, comma-grouped OR bare
# integer part, 2-decimal fraction, optional trailing sign / tax flag. This is
# the grid's full-fidelity form -- the widest pattern that still refuses
# non-amount tokens -- and the one the render path snaps columns with.
PRICE_TOKEN = re.compile(
    f"^[-+]?{_NF.currency}?{_integer_part(_NF, allow_bare=True)}"
    f"{_fraction(_NF)}[-+]?{_NF.tax_flag}?$"
)


def is_price_token(text: str) -> bool:
    """True for a right-aligned amount token (canonical form, spaces ignored)."""
    return bool(PRICE_TOKEN.match(str(text or "").strip().replace(" ", "")))


# --- historical variants (kept byte-identical for their call sites) --------

# font_profile._price_column_x: measures the real price column from OCR text.
# No signs (a stray leading ``-`` is noise, not a column member), no bare
# integer part, tolerates a trailing currency glyph OCR sometimes attaches.
PROFILE_PRICE_TOKEN = re.compile(
    f"^{_NF.currency}?{_integer_part(_NF)}(?:{_fraction(_NF)})"
    f"{_NF.currency}?{_NF.tax_flag}?$"
)

# render_synthetic_receipts cached-line examples: signs but no tax flag and no
# bare integer part (the cached corpus never carries either).
SYNTH_PRICE_TOKEN = re.compile(r"^[-+]?\$?\d{1,3}(?:,\d{3})*\.\d{2}[-+]?$")

# compose_dollartree: curled-photo OCR shatters prices into dotless fragments
# ("1251" = "1.25" + tax flag misread), so the body is deliberately loose and
# only the DT tax flag ``T`` is accepted.
DOLLARTREE_PRICE_TOKEN = re.compile(r"^\$?\d*\.?\d+T?$")

# glyph_renderer right-aligned stamping: up to a 4-digit ungrouped head,
# optional trailing currency / single flag letter / trailing minus.
GLYPH_AMOUNT_TOKEN = re.compile(r"^\$?\d{1,4}(?:,\d{3})*\.\d{2}\$?[A-Z]?-?$")
