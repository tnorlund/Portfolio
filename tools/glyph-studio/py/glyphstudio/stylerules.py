"""Section-classifier rules carried by ``fonts/<slug>/stylemap.json``.

A stylemap may declare its own ordered ``rules`` list so the measuring side
(``glyphstudio.stylescan``) and the rendering side
(``receipt_agent...rendering.receipt_stylemap``) classify rows from ONE
source instead of two hand-synchronised regex lists in code::

    "rules": [
      {"section": "tender_line", "pattern": "^(DEBIT|CREDIT)\\\\s*\\\\$?[\\\\d.,]*$"},
      {"section": "store_header", "pattern": "^SPEEDWAY\\\\b", "flags": "i"}
    ]

``flags`` defaults to ``"i"`` (case-insensitive); ``""`` makes a rule
case-sensitive. Rules are tried in order; the first match wins. Every
merchant-specific rule list lives here; merchants without ``rules`` fall back
to stylescan's in-code Sprouts rules (``stylescan._RULES``).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

FONTS_DIR = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
    "fonts",
)

_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "x": re.VERBOSE}


def compile_rules(
    stylemap: Mapping[str, Any] | None,
) -> list[tuple[str, re.Pattern]] | None:
    """Ordered ``(section, compiled_regex)`` pairs, or None when absent."""
    if not stylemap:
        return None
    raw = stylemap.get("rules")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[tuple[str, re.Pattern]] = []
    for entry in raw:
        section = str(entry["section"])
        flags = 0
        for ch in str(entry.get("flags", "i")):
            flags |= _FLAGS[ch]
        out.append((section, re.compile(str(entry["pattern"]), flags)))
    return out


def load_stylemap(
    font_dir_name: str, fonts_dir: str = FONTS_DIR
) -> dict | None:
    path = os.path.join(fonts_dir, font_dir_name, "stylemap.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rules_for_font(
    font_dir_name: str, fonts_dir: str = FONTS_DIR
) -> list[tuple[str, re.Pattern]] | None:
    return compile_rules(load_stylemap(font_dir_name, fonts_dir))


def rule_sections(stylemap: Mapping[str, Any] | None) -> set[str]:
    return {str(e["section"]) for e in (stylemap or {}).get("rules", []) or []}
