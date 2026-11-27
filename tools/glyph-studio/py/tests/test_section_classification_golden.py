"""Section classification is frozen against committed goldens.

Regenerate with ``python section_classification_golden.py`` (from
``tools/glyph-studio/py``) only for an intentional classifier change, and
say so in the PR; a refactor of where rules live must pass unchanged.
"""

import json

import pytest
import section_classification_golden as golden
from glyphstudio import stylescan


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_live_classifier_reproduces_golden():
    data = _load(golden.GOLDEN_PATH)
    assert data["slugs"] == list(golden.SLUGS)
    assert len(data["rows"]) > 1000
    mismatches = []
    for text, has_price, expected in data["rows"]:
        actual = golden.classify_row(text, has_price)
        if actual != expected:
            for slug, want, got in zip(golden.SLUGS, expected, actual):
                if want != got:
                    mismatches.append((slug, text, has_price, want, got))
    assert not mismatches, mismatches[:20]


@pytest.mark.parametrize("slug", golden.SLUGS)
def test_rules_for_merchant_is_pinned(slug):
    pinned = _load(golden.RULES_PIN_PATH)[slug]
    served = [
        [section, rx.pattern, golden.flag_chars(rx)]
        for section, rx in stylescan.rules_for_merchant(slug)
    ]
    assert served == pinned
