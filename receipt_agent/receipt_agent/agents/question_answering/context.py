"""Exact receipt aggregates and bounded, explicitly sampled model evidence.

The complete receipt corpus lives in graph state. Provider messages carry
computed totals and a bounded evidence view, never raw database records.
"""

import calendar
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MAX_CONTEXT_BYTES = 80_000
MAX_SUMMARY_ROWS = 20
MAX_MONTH_ROWS = 60
MAX_EVIDENCE_BYTES = 12_000
MAX_TOOL_LIST_BYTES = 3_000
COMPACT_AGGREGATE_NOTE = (
    "Fields ending in note_ref index shared_notes. An extrema same_as entry "
    "has exactly the same amount, tie count and representative receipt as "
    "the named sibling entry. These references only remove duplicate text "
    "and identical extrema; all scope facts remain exact."
)


class QAContextBudgetExceeded(ValueError):
    """A deterministic input-size failure; retrying it cannot help."""


def ensure_context_budget(messages: list) -> None:
    """Fail before provider invocation instead of retrying oversized input.

    UTF-8 bytes are a conservative proxy for byte-tokenized model input.
    Leave substantial room below the 131,072-token deployment limit for
    message framing, tool definitions, and completion tokens. Do not silently
    drop conversation turns, tool results, or financial evidence to fit.
    """
    size = len(
        json.dumps(
            [message.model_dump() for message in messages],
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    )
    if size > MAX_CONTEXT_BYTES:
        raise QAContextBudgetExceeded(
            f"QA evidence exceeds the {MAX_CONTEXT_BYTES}-byte input budget "
            f"({size} bytes). Narrow the date or merchant filters. "
            "No provider request was sent and no financial data was dropped."
        )


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _money(amount: Decimal) -> float:
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def sum_amounts(amounts: list[Any]) -> float:
    """Sum valid amounts using decimal arithmetic before rounding to cents."""
    return _money(
        sum((_decimal(amount) or Decimal(0) for amount in amounts), Decimal(0))
    )


def _totals(rows: list[dict]) -> dict:
    totals = {key: Decimal(0) for key in ("grand_total", "tax", "tip")}
    with_totals = 0
    for row in rows:
        for key in totals:
            amount = _decimal(row.get(key))
            if amount is not None:
                totals[key] += amount
                if key == "grand_total":
                    with_totals += 1
    return {
        "count": len(rows),
        "total_spending": _money(totals["grand_total"]),
        "total_tax": _money(totals["tax"]),
        "total_tip": _money(totals["tip"]),
        "receipts_with_totals": with_totals,
        "receipts_missing_totals": len(rows) - with_totals,
        "average_receipt": (
            _money(totals["grand_total"] / with_totals)
            if with_totals
            else None
        ),
    }


def receipt_total_extrema(
    rows: list[dict], *, excluded_outliers: list[dict] | None = None
) -> dict:
    """Keep exact extrema and one stable receipt per value, including ties.

    This is separate from period totals: a small monthly sum does not identify
    the cheapest receipt. Negative refunds remain in signed extrema, while the
    nonnegative minimum also supports purchase comparisons without refunds.
    """
    extrema: dict[str, dict | None] = {
        "minimum": None,
        "minimum_nonnegative": None,
        "maximum": None,
    }
    for row in rows:
        amount = _decimal(row.get("grand_total"))
        if amount is None:
            continue
        key = (str(row.get("image_id", "")), str(row.get("receipt_id", "")))
        for name, previous in extrema.items():
            if name == "minimum_nonnegative" and amount < 0:
                continue
            better = previous is None or (
                amount > previous["value"]
                if name == "maximum"
                else amount < previous["value"]
            )
            if better:
                extrema[name] = {
                    "value": amount,
                    "count": 1,
                    "key": key,
                    "row": row,
                }
            elif amount == previous["value"]:
                previous["count"] += 1
                if key < previous["key"]:
                    previous["key"] = key
                    previous["row"] = row

    result: dict[str, Any] = {}
    for name, extreme in extrema.items():
        if extreme is None:
            result[name] = None
            continue
        row = extreme["row"]
        receipt = {
            "image_id": row.get("image_id"),
            "receipt_id": row.get("receipt_id"),
            "grand_total": _money(extreme["value"]),
            "date": row.get("effective_date") or row.get("date"),
            "date_source": row.get("date_source"),
        }
        merchant = row.get("merchant_name") or row.get("merchant")
        if len(str(merchant).encode("utf-8")) <= 512:
            receipt["merchant"] = merchant
        else:
            receipt["merchant_omitted"] = (
                "Exceeds display budget; retained in state."
            )
        result[name] = {
            "amount": _money(extreme["value"]),
            "matching_receipts": extreme["count"],
            "representative_receipt": receipt,
        }
    result["population"] = "accepted_receipts"
    result["excluded_outlier_count"] = len(excluded_outliers or [])
    if excluded_outliers:
        excluded = [
            amount
            for row in excluded_outliers
            if (amount := _decimal(row.get("grand_total"))) is not None
        ]
        result["excluded_outlier_range"] = {
            "minimum_total": _money(min(excluded)) if excluded else None,
            "maximum_total": _money(max(excluded)) if excluded else None,
            "note": (
                "Flagged by the existing OCR-outlier heuristic; not confirmed "
                "errors. These receipts are excluded from accepted extrema "
                "and spending totals and require review."
            ),
        }
    result["note"] = (
        "Exact receipt grand-total extrema among accepted receipts in this scope, "
        "not product prices or monthly totals. Missing/invalid totals are "
        "excluded. Minimum and maximum include negative refunds; "
        "minimum_nonnegative excludes negative totals and includes zero. "
        "Each value retains one stable receipt; matching_receipts counts ties. "
        "When excluded_outlier_count is positive, the maximum is only the "
        "largest among accepted receipts, not all matching purchases."
    )
    return result


def aggregate_receipts(
    rows: list[dict],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    excluded_outliers: list[dict] | None = None,
) -> dict:
    """Aggregate every receipt using decimal arithmetic, including refunds.

    Temporal totals exclude undated receipts explicitly. Calendar-month
    averages include zero-receipt months between the first and last dated
    receipts; both boundary months can be partial. An observed-month average
    is also provided so the denominator is never left to model inference.
    """
    months: dict[str, list[dict]] = {}
    weekdays: dict[int, list[dict]] = {day: [] for day in range(7)}
    undated: list[dict] = []
    dated: list[dict] = []
    dates: list[str] = []
    bank_date_count = 0
    for row in rows:
        raw_date = row.get("effective_date") or row.get("date")
        try:
            parsed = datetime.fromisoformat(str(raw_date)).date()
        except (ValueError, TypeError):
            undated.append(row)
            continue
        dated.append(row)
        dates.append(parsed.isoformat())
        bank_date_count += row.get("date_source") == "bank"
        months.setdefault(parsed.strftime("%Y-%m"), []).append(row)
        weekdays[parsed.weekday()].append(row)

    first = min(dates) if dates else None
    last = max(dates) if dates else None
    month_count = (
        (int(last[:4]) - int(first[:4])) * 12
        + int(last[5:7])
        - int(first[5:7])
        + 1
        if first and last
        else 0
    )
    requested_month_count = 0
    if start_date and end_date:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        requested_month_count = (
            (end.year - start.year) * 12 + end.month - start.month + 1
        )
    dated_total = sum(
        (_decimal(row.get("grand_total")) or Decimal(0) for row in dated),
        Decimal(0),
    )
    return {
        **_totals(rows),
        "receipt_total_extrema": receipt_total_extrema(
            rows, excluded_outliers=excluded_outliers
        ),
        "date_coverage": {
            "first_date": first,
            "last_date": last,
            "dated_receipts": len(dated),
            "bank_date_receipts": bank_date_count,
            "undated": _totals(undated),
            "calendar_month_count": month_count,
            "observed_month_count": len(months),
            "dated_total_spending": _money(dated_total),
            "average_per_calendar_month": (
                _money(dated_total / month_count) if month_count else None
            ),
            "average_per_observed_month": (
                _money(dated_total / len(months)) if months else None
            ),
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "requested_calendar_month_count": requested_month_count or None,
            "average_per_requested_calendar_month": (
                _money(dated_total / requested_month_count)
                if requested_month_count > 0
                else None
            ),
            "note": (
                "Monthly and weekday totals use only dated receipts. "
                "Calendar-month average includes months with no receipts "
                "between first_date and last_date; boundary months may be "
                "partial. When both date filters are supplied, the requested "
                "calendar-month average also includes empty boundary months "
                "of that interval. This describes recorded receipts, not complete "
                "personal spending. Missing totals are not assumed known."
            ),
        },
        "monthly_spending": [
            {"month": month, **_totals(months[month])}
            for month in sorted(months)
        ],
        "weekday_spending": [
            {"weekday": calendar.day_name[day], **_totals(weekdays[day])}
            for day in range(7)
        ],
    }


def aggregate_view(aggregate: dict, month_offset: int = 0) -> dict:
    """Page month buckets without changing complete totals or denominators."""
    result = dict(aggregate)
    months = aggregate.get("monthly_spending", [])
    result["monthly_spending"] = months[
        month_offset : month_offset + MAX_MONTH_ROWS
    ]
    next_offset = month_offset + len(result["monthly_spending"])
    result["month_coverage"] = {
        "total_groups": len(months),
        "offset": month_offset,
        "returned_groups": len(result["monthly_spending"]),
        "next_offset": next_offset if next_offset < len(months) else None,
        "note": "All totals and averages include all groups, across pages.",
    }
    return result


def compact_aggregate_notes(aggregate: dict, shared_notes: list[str]) -> dict:
    """Deduplicate explanatory text and identical extrema without losing facts.

    The returned view owns every nested object; retained scope data is never
    mutated. Note references index the synthesis context's shared_notes list.
    Extrema aliases reference another entry in the same receipt_total_extrema.
    """

    def compact(value: Any) -> Any:
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if (key == "note" or key.endswith("_note")) and isinstance(
                item, str
            ):
                if item not in shared_notes:
                    shared_notes.append(item)
                result[f"{key}_ref"] = shared_notes.index(item)
            else:
                result[key] = compact(item)
        return result

    result = compact(aggregate)
    extrema = result.get("receipt_total_extrema", {})
    originals = dict(extrema)
    names = ("minimum", "minimum_nonnegative", "maximum")
    for index, name in enumerate(names):
        if originals.get(name) is None:
            continue
        for earlier in names[:index]:
            if originals[name] == originals.get(earlier):
                extrema[name] = {"same_as": earlier}
                break
    return result


def bounded_rows(
    rows: list, *, limit: int = MAX_SUMMARY_ROWS, offset: int = 0
) -> tuple:
    """Sample whole records, preserving complete records outside the prompt."""
    selected = []
    used = 0
    for row in rows[offset : offset + limit]:
        size = len(json.dumps(row, default=str).encode("utf-8"))
        if used + size > MAX_TOOL_LIST_BYTES:
            if not selected:
                # Advance past a whole oversized record only with an explicit
                # placeholder. Never cut an amount, identifier or JSON string.
                selected.append(
                    {
                        "record_omitted": True,
                        "source_offset": offset,
                        "reason": (
                            "This single record exceeds the evidence page "
                            "budget. Its complete contents remain in state."
                        ),
                    }
                )
            break
        selected.append(row)
        used += size
    return selected, {
        "total_count": len(rows),
        "returned_count": len(selected),
        "offset": offset,
        "next_offset": (
            offset + len(selected)
            if offset + len(selected) < len(rows)
            else None
        ),
        "note": (
            "Rows are sampled; the complete result is retained for synthesis. "
            "Do not infer totals or exhaustive coverage from this sample."
        ),
    }


def tool_result_view(
    result: dict, *, compact: bool = False, offset: int = 0
) -> dict:
    """Bound detail, search and discovery outputs, retaining exact metadata."""
    view = {}
    coverage = {}
    for key, value in result.items():
        if key == "words_by_line":
            continue  # Raw coordinates/labels stay in retained receipt state.
        if key == "formatted_receipt":
            lines, coverage[key] = bounded_rows(
                value.splitlines(),
                limit=0 if compact else MAX_SUMMARY_ROWS,
                offset=offset,
            )
            view[key] = "\n".join(
                line if isinstance(line, str) else json.dumps(line)
                for line in lines
            )
        elif isinstance(value, list):
            view[key], coverage[key] = bounded_rows(
                value, limit=0 if compact else MAX_SUMMARY_ROWS, offset=offset
            )
        else:
            view[key] = value
    if coverage:
        view["result_coverage"] = coverage
    return view


def amount_aggregate_view(aggregate: dict) -> dict:
    """Retain the exact filtered amount result with a bounded audit sample."""
    result = {
        key: value
        for key, value in aggregate.items()
        if key
        not in (
            "breakdown",
            "excluded_outliers",
            "excluded_ambiguous_lines",
            "source_receipts",
        )
    }
    breakdown = aggregate.get("breakdown", [])
    sample, _ = bounded_rows(breakdown, limit=5)
    result["breakdown"] = sample
    result["breakdown_coverage"] = {
        "total_count": len(breakdown),
        "returned_count": len(sample),
        "note": (
            "Only the audit rows are sampled. The filtered total and count "
            "include every unambiguous matching amount in the retrieved "
            "receipt scope after disclosed exclusions. "
            "Whole-receipt totals must not substitute for this item total."
        ),
    }
    excluded = aggregate.get("excluded_ambiguous_lines", [])
    if excluded:
        samples = [
            {
                "image_id": row.get("image_id"),
                "receipt_id": row.get("receipt_id"),
                "line_idx": row.get("line_idx"),
                "amounts": [
                    amount.get("amount") for amount in row["amounts"][:5]
                ],
                "total_amount_count": len(row["amounts"]),
                "returned_amount_count": min(5, len(row["amounts"])),
            }
            for row in excluded[:5]
        ]
        sample, _ = bounded_rows(samples, limit=5)
        result["excluded_ambiguous_line_sample"] = sample
        result["excluded_ambiguous_line_coverage"] = {
            "total_count": len(excluded),
            "returned_count": len(sample),
            "note": (
                "These prices are excluded, not confirmed product spending. "
                "Full excluded rows and all prices remain in state."
            ),
        }
    return result


def receipt_evidence_view(
    rows: list[dict], *, offset: int = 0, limit: int = MAX_SUMMARY_ROWS
) -> dict:
    """Project receipt evidence with an explicit count and byte-bounded page."""
    selected = []
    used = 0
    for row in rows[offset : offset + min(limit, MAX_SUMMARY_ROWS)]:
        compact = {
            key: row.get(key)
            for key in (
                "image_id",
                "receipt_id",
                "merchant_name",
                "grand_total",
                "tax",
                "tip",
                "effective_date",
                "date",
                "date_source",
                "item_count",
            )
        }
        if "merchant" in row:
            compact["merchant_name"] = row["merchant"]
        # Include line items only if the whole receipt fits; no partial money
        # strings or arbitrarily cut JSON. Full details remain in graph state.
        if row.get("line_items"):
            compact["line_items"] = row["line_items"]
        size = len(json.dumps(compact, default=str).encode("utf-8"))
        if used + size > MAX_EVIDENCE_BYTES:
            compact.pop("line_items", None)
            compact["line_items_omitted"] = len(row.get("line_items", []))
            size = len(json.dumps(compact, default=str).encode("utf-8"))
        if used + size > MAX_EVIDENCE_BYTES:
            if not selected:
                selected.append(
                    {
                        "record_omitted": True,
                        "source_offset": offset,
                        "reason": (
                            "This receipt's metadata exceeds the evidence "
                            "page budget. All financial fields remain in "
                            "state and in the complete aggregates."
                        ),
                    }
                )
            break
        selected.append(compact)
        used += size
    next_offset = offset + len(selected)
    return {
        "summaries": selected,
        "summary_coverage": {
            "total_count": len(rows),
            "returned_count": len(selected),
            "offset": offset,
            "next_offset": next_offset if next_offset < len(rows) else None,
            "note": (
                "Receipt rows are a bounded evidence page, not the total "
                "population. All matching receipts are retained in state "
                "and included in aggregates. Never sum this page to infer "
                "corpus totals; request another offset or get_receipt for "
                "details when needed."
            ),
        },
    }
