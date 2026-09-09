"""Receipt-coverage check for ``none:<merchant_slug>:<date>`` meal items.

A meal item with no receipt line asks the receipts-email tools whether a
card charge at that merchant exists without a matched receipt. The tools
prove less than the question asks: ``get_unmatched(kind="txns")`` returns a
bounded, most-recent-first list with no merchant filter and no pagination
cursor, and ``get_coverage`` reports receipt-match percentages, not an
import-completeness interval. Outcomes are therefore scoped to what the
returned data proves:

``candidate_charge_no_receipt``
    An unmatched charge whose description matches the merchant sits inside
    the window. A candidate for the purchase, never proof of it.
``indeterminate``
    No candidate in the returned list. Absence is not established because
    the list is not an exhaustive enumeration (``reason`` says why).
``no_unmatched_charge_in_window``
    Only when the backend declares the window exhaustively searched
    (``coverage()["exhaustive"] is True``) and the unmatched list was not
    truncated by ``limit``. The real receipts-email backend does not set
    that field today; adding it (with a merchant filter and cursor on
    ``get_unmatched``) is the external dependency tracked in the sprint
    plan. Until it lands every negative is ``indeterminate``.
``lookup_unavailable``
    No backend, the external repo cannot be imported, or any call raised.

This module never assumes or produces a price: a candidate's ``amount`` is
the card charge echoed from the backend row, and the meal item stays
``unknown``. Pure apart from :class:`EmlrecBackend`; no DynamoDB, no
network.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from typing import Any, Callable, Protocol

from receipt_dynamo.merchant_truth_loader import normalize_merchant_alias

OUTCOME_CANDIDATE = "candidate_charge_no_receipt"
OUTCOME_INDETERMINATE = "indeterminate"
OUTCOME_NO_CHARGE = "no_unmatched_charge_in_window"
OUTCOME_UNAVAILABLE = "lookup_unavailable"
OUTCOMES = (
    OUTCOME_CANDIDATE,
    OUTCOME_INDETERMINATE,
    OUTCOME_NO_CHARGE,
    OUTCOME_UNAVAILABLE,
)

REASON_NO_BACKEND = "no_backend"
REASON_NO_MERCHANT_FILTER = "no_merchant_filter"
REASON_LIST_TRUNCATED = "list_truncated"
REASON_EMPTY_WINDOW = "window_ends_before_it_starts"
REASON_UNPARSEABLE_ROW = "unparseable_row"

DEFAULT_WINDOW_DAYS = 14
DEFAULT_LIMIT = 200
DEFAULT_REPO = "~/receipts-email"
REPO_ENV = "RECEIPTS_EMAIL_REPO"
DB_ENV = "RECEIPTS_EMAIL_DB"

# Field names of one ``get_unmatched(kind="txns")`` row, verbatim from the
# SELECT in receipts-email ``emlrec/queries.py::unmatched``.
UNMATCHED_ROW_FIELDS = (
    "txn_id",
    "account",
    "txn_date",
    "description",
    "amount",
    "txn_class",
)


class CoverageBackend(Protocol):
    """The two receipts-email calls the check depends on."""

    def unmatched_transactions(
        self, *, start_date: date, end_date: date, limit: int
    ) -> list[dict[str, Any]]:
        """Rows of ``get_unmatched(kind="txns")`` for the date range.

        Each row carries :data:`UNMATCHED_ROW_FIELDS`; ``txn_date`` is an
        ISO ``YYYY-MM-DD`` string and ``amount`` is the charge in dollars.
        """

    def coverage(self, *, start_date: date, end_date: date) -> dict[str, Any]:
        """The ``get_coverage`` result for the date range.

        Shaped ``{"period", "receiptable_only", "rows": [...]}``. A future
        backend may add ``"exhaustive": True`` to declare the range fully
        imported and exhaustively searched; only that exact value lets the
        check report a negative.
        """


def coverage_window(
    on: date, as_of: date, window_days: int = DEFAULT_WINDOW_DAYS
) -> tuple[date, date]:
    """``[on - window_days, min(on + window_days, as_of)]``; never past as_of."""
    if window_days < 0:
        raise ValueError("window_days must not be negative")
    start = on - timedelta(days=window_days)
    end = min(on + timedelta(days=window_days), as_of)
    return start, end


def merchant_tokens(value: str) -> list[str]:
    """Normalized tokens with a possessive ``s`` folded into its word.

    ``normalize_merchant_alias`` splits ``TRADER JOE'S`` into ``trader joe
    s`` while a card prints ``TRADER JOES``; folding gives ``trader joes``
    on both sides.
    """
    tokens: list[str] = []
    for token in normalize_merchant_alias(value).split():
        if token == "s" and tokens:
            tokens[-1] += "s"
        else:
            tokens.append(token)
    return tokens


def _abbreviates(short: str, word: str) -> bool:
    """``whse`` for ``wholesale``, ``mkt`` for ``market``: same first letter,
    two or more letters, all of them in order inside the word."""
    if len(short) < 2 or len(short) >= len(word) or short[0] != word[0]:
        return False
    position = 0
    for char in short:
        position = word.find(char, position)
        if position < 0:
            return False
        position += 1
    return True


def _token_matches(description_token: str, slug_token: str) -> bool:
    return description_token == slug_token or _abbreviates(
        description_token, slug_token
    )


def merchant_matches(merchant_slug: str, description: str) -> bool:
    """Casefold token test between a merchant slug and a card description.

    Both sides go through ``merchant_tokens``. Every slug token must be
    matched, in order and contiguously, by a description token that equals
    it or abbreviates it, so ``COSTCO WHSE #1234`` matches
    ``costco-wholesale`` and ``TRADER JOE'S #123`` matches ``trader-joe-s``
    while ``TRADER VICS`` and ``COST PLUS`` match neither.
    """
    slug_tokens = merchant_tokens(merchant_slug)
    description_tokens = merchant_tokens(description)
    width = len(slug_tokens)
    if not width or width > len(description_tokens):
        return False
    return any(
        all(
            _token_matches(description_token, slug_token)
            for description_token, slug_token in zip(
                description_tokens[index : index + width], slug_tokens
            )
        )
        for index in range(len(description_tokens) - width + 1)
    )


def _names_a_merchant(description: str) -> bool:
    """A description with only a store number (``#1234``) names nobody."""
    return any(not token.isdigit() for token in merchant_tokens(description))


def _row_date(row: dict[str, Any]) -> date | None:
    raw = row.get("txn_date")
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _candidate(row: dict[str, Any], on: date) -> dict[str, Any]:
    return {
        "txn_id": row.get("txn_id"),
        "date": on.isoformat(),
        "amount": row.get("amount"),
        "description": row.get("description"),
    }


def _result(
    outcome: str,
    reason: str | None,
    window: tuple[date, date] | None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "reason": reason,
        "window": (
            None
            if window is None
            else [window[0].isoformat(), window[1].isoformat()]
        ),
        "candidates": list(candidates or []),
    }


def check_coverage(
    *,
    merchant_slug: str,
    on: date,
    as_of: date,
    backend: CoverageBackend | None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Classify a ``none:`` item against the unmatched card charges.

    Returns ``{"outcome", "reason", "window": [start, end], "candidates"}``
    with ``candidates`` populated only for ``candidate_charge_no_receipt``.
    Rows the backend returns outside the window are ignored. A negative is
    reported only when the backend declares the window exhaustive, the list
    was not cut off by ``limit``, and every row carried a readable
    description and date (a row whose merchant cannot be told is an
    unresolved candidate, so it forces ``indeterminate``).
    """
    if not merchant_slug or not merchant_tokens(merchant_slug):
        raise ValueError("merchant_slug must contain at least one token")
    if limit <= 0:
        raise ValueError("limit must be positive")
    window = coverage_window(on, as_of, window_days)
    if backend is None:
        return _result(OUTCOME_UNAVAILABLE, REASON_NO_BACKEND, window)
    start, end = window
    if end < start:
        return _result(OUTCOME_INDETERMINATE, REASON_EMPTY_WINDOW, window)
    try:
        rows = backend.unmatched_transactions(
            start_date=start, end_date=end, limit=limit
        )
        rows = list(rows)
        candidates: list[dict[str, Any]] = []
        unparseable = False
        for row in rows:
            if not isinstance(row, dict):
                unparseable = True
                continue
            description = row.get("description")
            if not isinstance(description, str) or not _names_a_merchant(
                description
            ):
                unparseable = True
                continue
            if not merchant_matches(merchant_slug, description):
                continue
            row_date = _row_date(row)
            if row_date is None:
                unparseable = True
                continue
            if start <= row_date <= end:
                candidates.append(_candidate(row, row_date))
        if candidates:
            candidates.sort(
                key=lambda item: (item["date"], str(item["txn_id"]))
            )
            return _result(OUTCOME_CANDIDATE, None, window, candidates)
        if unparseable:
            return _result(
                OUTCOME_INDETERMINATE, REASON_UNPARSEABLE_ROW, window
            )
        if len(rows) >= limit:
            return _result(
                OUTCOME_INDETERMINATE, REASON_LIST_TRUNCATED, window
            )
        report = backend.coverage(start_date=start, end_date=end)
    except Exception as exc:  # noqa: BLE001 - any failure is an outcome
        return _result(
            OUTCOME_UNAVAILABLE,
            f"backend_error:{type(exc).__name__}",
            window,
        )
    if isinstance(report, dict) and report.get("exhaustive") is True:
        return _result(OUTCOME_NO_CHARGE, None, window)
    return _result(OUTCOME_INDETERMINATE, REASON_NO_MERCHANT_FILTER, window)


def coverage_checker(
    backend: CoverageBackend | None,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> Callable[..., dict[str, Any]]:
    """Bind a backend into the ``(merchant_slug, on, as_of)`` hook the
    meal builder calls; every argument is keyword-only."""

    def check(*, merchant_slug: str, on: date, as_of: date) -> dict[str, Any]:
        return check_coverage(
            merchant_slug=merchant_slug,
            on=on,
            as_of=as_of,
            backend=backend,
            window_days=window_days,
            limit=limit,
        )

    return check


class EmlrecBackend:
    """Read the receipts-email SQLite database through its own queries.

    receipts-email is a separate, optional repository, so ``emlrec`` is not
    a dependency of this package: the constructor puts the repo path on
    ``sys.path`` and imports it lazily. A missing repo raises ``ImportError``
    from the constructor, which callers map to ``lookup_unavailable`` by
    passing ``backend=None``. Connections are opened read-only per call with
    the same row factory ``emlrec.db.connect`` uses, and the two methods
    call exactly the query functions receipts-email's ``server.py`` calls
    for ``get_unmatched`` and ``get_coverage``.
    """

    def __init__(
        self, repo_path: str | None = None, db_path: str | None = None
    ) -> None:
        repo = repo_path or os.environ.get(REPO_ENV) or DEFAULT_REPO
        self.repo_path = os.path.abspath(os.path.expanduser(repo))
        if not os.path.isdir(self.repo_path):
            raise ImportError(
                f"receipts-email repo not found at {self.repo_path}"
            )
        if self.repo_path not in sys.path:
            sys.path.insert(0, self.repo_path)
        try:
            from emlrec import db as emlrec_db
            from emlrec import queries as emlrec_queries
        except ImportError as exc:
            raise ImportError(
                f"emlrec not importable from {self.repo_path}: {exc}"
            ) from exc
        self._queries = emlrec_queries
        configured = db_path or os.environ.get(DB_ENV) or emlrec_db.DB_PATH
        self.db_path = os.path.abspath(os.path.expanduser(configured))

    def _connect(self) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(self.db_path)
        conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True, timeout=60
        )
        conn.row_factory = sqlite3.Row
        return conn

    def unmatched_transactions(
        self, *, start_date: date, end_date: date, limit: int
    ) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            result = self._queries.unmatched(
                conn,
                kind="txns",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                limit=limit,
            )
        return [dict(row) for row in result["rows"]]

    def coverage(self, *, start_date: date, end_date: date) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            return self._queries.coverage(
                conn,
                period="month",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )


__all__ = [
    "CoverageBackend",
    "EmlrecBackend",
    "OUTCOMES",
    "OUTCOME_CANDIDATE",
    "OUTCOME_INDETERMINATE",
    "OUTCOME_NO_CHARGE",
    "OUTCOME_UNAVAILABLE",
    "UNMATCHED_ROW_FIELDS",
    "check_coverage",
    "coverage_checker",
    "coverage_window",
    "merchant_matches",
]


def default_coverage_checker(
    *, window_days: int = 14, limit: int = 200
) -> Callable[..., dict[str, Any]] | None:
    """The builder's default: the receipts-email backend when it is present.

    A missing repository or database is an unavailable lookup, not an error,
    so construction failures return ``None`` and the caller reports
    ``lookup_unavailable``.
    """
    try:
        backend = EmlrecBackend()
    except (ImportError, FileNotFoundError, OSError):
        return None
    return coverage_checker(backend, window_days=window_days, limit=limit)
