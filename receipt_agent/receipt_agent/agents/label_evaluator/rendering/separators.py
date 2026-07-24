"""Separator anchor sources and vertical layout for receipt rendering."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Protocol

from receipt_agent.agents.label_evaluator.rendering.number_format import (
    US,
    date_core,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridWord,
    is_price_token,
)


class SeparatorConfig(Protocol):
    """The RenderConfig fields consumed by separator sources."""

    dashed_separators: bool
    dash_around_phrases: tuple
    dash_after_amount_date: bool
    total_include_tokens: tuple[str, ...]
    total_exclude_tokens: tuple[str, ...]


SeparatorSource = Callable[
    [
        Sequence[Sequence[GridWord]],
        Sequence[str],
        SeparatorConfig,
    ],
    set[int],
]


def _is_final_total(
    row_text: str,
    include: tuple[str, ...] = ("TOTAL",),
    exclude: tuple[str, ...] = ("SUBTOTAL", "NUMBER", "SOLD", "TAX"),
) -> bool:
    """True for a grand-total row, excluding summary lookalikes."""
    text = row_text.upper()
    if not all(token in text for token in include):
        return False
    return not any(token in text for token in exclude)


_DATE_LED = re.compile(f"^{date_core(US)}$")


def _separator_anchor_rows(
    rows: Sequence[Sequence[GridWord]],
    row_texts: Sequence[str],
    config: SeparatorConfig,
) -> set[int]:
    """Rows after which a profile-requested dashed separator is printed.

    A grand-total rule needs both the configured total wording and a currency
    amount. The amount requirement prevents item-count rows such as
    ``TOTAL ... ITEMS SOLD = 4`` from becoming separators when OCR fragments
    the configured exclusion word. ``total_exclude_tokens`` additionally
    rejects summary lookalikes such as ``TOTAL TAX``.
    """
    anchors: set[int] = set()
    if not (config.dashed_separators or config.dash_around_phrases):
        return anchors
    for index, (line, text) in enumerate(zip(rows, row_texts)):
        previous = row_texts[index - 1] if index > 0 else ""
        first = text.split()[0] if text.split() else ""
        is_total_row = (
            config.dashed_separators
            and any(is_price_token(word.text) for word in line)
            and _is_final_total(
                text,
                config.total_include_tokens,
                config.total_exclude_tokens,
            )
        )
        is_amount_date = (
            config.dashed_separators
            and config.dash_after_amount_date
            and "AMOUNT" in previous
            and bool(_DATE_LED.match(first))
        )
        if is_total_row or is_amount_date:
            anchors.add(index)
        for phrase in config.dash_around_phrases or ():
            if text.startswith(phrase.upper()) and any(
                is_price_token(word.text) for word in line
            ):
                anchors.add(index)
                if index > 0:
                    anchors.add(index - 1)
    return anchors


# _render_grid iterates this collection. A new independent separator detector
# can land as a new function/module plus one list entry instead of editing the
# shared renderer control flow.
SEPARATOR_SOURCES: tuple[SeparatorSource, ...] = (_separator_anchor_rows,)


def _separator_layout(
    baselines: Sequence[float],
    dash_after_rows: set[int],
    *,
    pitch: float,
    cap_h: float,
) -> tuple[list[float], list[float]]:
    """Place separator baselines in existing whitespace when possible.

    A thin dash row only needs the whitespace between the current row's
    descender and the next row's cap. Existing gaps are left untouched. For a
    genuinely cramped pair, add only the missing clearance rather than a full
    pitch. This keeps later rows stable while still preventing overprint.
    """
    adjusted = [float(value) for value in baselines]
    dash_ys: list[float] = []
    if not adjusted or not dash_after_rows:
        return adjusted, dash_ys

    pitch = max(1.0, float(pitch))
    cap_h = max(1.0, float(cap_h))
    # Current descenders consume about .22 cap and the next row consumes one
    # cap above its baseline. The remaining .18 cap gives the dash ink and a
    # small paper gap on each side.
    required_gap = max(pitch, cap_h * 1.40)
    for index in sorted(dash_after_rows):
        if index < 0 or index >= len(adjusted):
            continue
        current = adjusted[index]
        if index + 1 >= len(adjusted):
            dash_ys.append(current + cap_h * 0.90)
            continue

        gap = adjusted[index + 1] - current
        extra = max(0.0, required_gap - gap)
        if extra:
            for following in range(index + 1, len(adjusted)):
                adjusted[following] += extra
            gap += extra

        whitespace_start = current + cap_h * 0.22
        whitespace_end = adjusted[index + 1] - cap_h
        if whitespace_end > whitespace_start:
            ink_target = (whitespace_start + whitespace_end) / 2.0
        else:
            ink_target = current + gap / 2.0
        # ``draw_token_chars`` receives a text baseline. A hyphen's ink sits
        # around half a cap above that baseline, so lower the draw baseline to
        # put the visible dash—not its invisible text baseline—in whitespace.
        dash_ys.append(ink_target + cap_h * 0.55)
    return adjusted, dash_ys
