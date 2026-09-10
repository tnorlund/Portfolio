"""Absence of a charge is asserted only when the backend proves the window."""

import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from receipt_nutrition.coverage import (
    OUTCOME_CANDIDATE,
    OUTCOME_INDETERMINATE,
    OUTCOME_NO_CHARGE,
    OUTCOME_UNAVAILABLE,
    UNMATCHED_ROW_FIELDS,
    EmlrecBackend,
    check_coverage,
    coverage_checker,
    coverage_window,
    merchant_matches,
)

ON = date(2026, 9, 6)
AS_OF = date(2026, 9, 9)


def txn(
    txn_id: str, txn_date: str, description: str, amount: float
) -> dict[str, Any]:
    """One ``get_unmatched(kind="txns")`` row with the real field names."""
    row = {
        "txn_id": txn_id,
        "account": "chase-sapphire",
        "txn_date": txn_date,
        "description": description,
        "amount": amount,
        "txn_class": "in-person",
    }
    assert tuple(row) == UNMATCHED_ROW_FIELDS
    return row


COSTCO = txn("t-costco", "2026-09-04", "COSTCO WHSE #1234", 187.42)
TRADER_JOES = txn("t-tj", "2026-08-15", "TRADER JOE'S #123", 42.10)
SPROUTS = txn("t-sprouts", "2026-09-05", "SPROUTS FARMERS MKT", 31.07)
COST_PLUS = txn("t-costplus", "2026-09-05", "COST PLUS WORLD MKT", 19.99)
LATE_COSTCO = txn("t-late", "2026-09-15", "COSTCO WHSE #1234", 12.00)


def coverage_report(**extra: Any) -> dict[str, Any]:
    """The real ``get_coverage`` shape, plus any future fields."""
    return {
        "period": "month",
        "receiptable_only": False,
        "rows": [
            {
                "period": "2026-09",
                "account": "chase-sapphire",
                "txns": 12,
                "matched_txns": 9,
                "spend": 640.12,
                "matched_spend": 512.00,
                "coverage_by_count": 0.75,
                "coverage_by_spend": 0.8,
            }
        ],
        **extra,
    }


class FakeBackend:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        report: dict[str, Any] | None = None,
        *,
        fail: Exception | None = None,
        fail_coverage: Exception | None = None,
    ) -> None:
        self.rows = rows
        self.report = coverage_report() if report is None else report
        self.fail = fail
        self.fail_coverage = fail_coverage
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def unmatched_transactions(
        self, *, start_date: date, end_date: date, limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "unmatched",
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                },
            )
        )
        if self.fail is not None:
            raise self.fail
        return list(self.rows)

    def coverage(self, *, start_date: date, end_date: date) -> dict[str, Any]:
        self.calls.append(
            ("coverage", {"start_date": start_date, "end_date": end_date})
        )
        if self.fail_coverage is not None:
            raise self.fail_coverage
        return self.report


def test_window_is_clipped_by_as_of() -> None:
    assert coverage_window(ON, AS_OF) == (date(2026, 8, 23), AS_OF)
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=None
    )
    assert result["window"] == ["2026-08-23", "2026-09-09"]


def test_window_stays_open_when_as_of_is_far_ahead() -> None:
    assert coverage_window(ON, date(2026, 12, 1)) == (
        date(2026, 8, 23),
        date(2026, 9, 20),
    )


def test_backend_receives_the_clipped_window_and_limit() -> None:
    backend = FakeBackend([])
    check_coverage(
        merchant_slug="costco-wholesale",
        on=ON,
        as_of=AS_OF,
        backend=backend,
        limit=50,
    )
    assert backend.calls[0] == (
        "unmatched",
        {"start_date": date(2026, 8, 23), "end_date": AS_OF, "limit": 50},
    )


def test_unmatched_costco_charge_in_window_is_a_candidate() -> None:
    backend = FakeBackend([SPROUTS, COSTCO, COST_PLUS])
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_CANDIDATE
    assert result["reason"] is None
    assert result["candidates"] == [
        {
            "txn_id": "t-costco",
            "date": "2026-09-04",
            "amount": 187.42,
            "description": "COSTCO WHSE #1234",
        }
    ]
    assert [name for name, _ in backend.calls] == ["unmatched"]


def test_trader_joes_spelling_matches_its_slug() -> None:
    backend = FakeBackend([TRADER_JOES])
    result = check_coverage(
        merchant_slug="trader-joe-s",
        on=date(2026, 8, 15),
        as_of=AS_OF,
        backend=backend,
    )
    assert result["outcome"] == OUTCOME_CANDIDATE
    assert result["candidates"][0]["txn_id"] == "t-tj"


@pytest.mark.parametrize(
    ("slug", "description", "expected"),
    [
        ("costco-wholesale", "COSTCO WHSE #1234", True),
        ("costco-wholesale", "Costco Wholesale #0482", True),
        ("trader-joe-s", "TRADER JOE'S #123", True),
        ("trader-joe-s", "TRADER JOES 0451", True),
        ("99-ranch-market", "99 RANCH MARKET #12", True),
        ("99-ranch-market", "SQ *RANCH 99", False),
        ("trader-joe-s", "TRADER VICS #123", False),
        ("costco-wholesale", "COSTCO GAS #1234", False),
        ("costco-wholesale", "COSTCO", False),
        ("costco-wholesale", "COST PLUS WORLD MKT", False),
        ("costco-wholesale", "CVS/PHARMACY #0482", False),
        ("costco-wholesale", "SPROUTS FARMERS MKT", False),
        ("sprouts-farmers-market", "SPROUTS FARMERS MKT", True),
        ("costco-wholesale", "", False),
    ],
)
def test_merchant_matching(
    slug: str, description: str, expected: bool
) -> None:
    assert merchant_matches(slug, description) is expected


def test_candidate_outside_window_is_ignored() -> None:
    backend = FakeBackend([LATE_COSTCO])
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_INDETERMINATE
    assert result["candidates"] == []


def test_no_match_is_indeterminate_without_a_merchant_filter() -> None:
    backend = FakeBackend([SPROUTS, COST_PLUS])
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result == {
        "outcome": OUTCOME_INDETERMINATE,
        "reason": "no_merchant_filter",
        "window": ["2026-08-23", "2026-09-09"],
        "candidates": [],
    }
    assert [name for name, _ in backend.calls] == ["unmatched", "coverage"]


def test_negative_requires_the_exhaustive_flag() -> None:
    backend = FakeBackend([SPROUTS], coverage_report(exhaustive=True))
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_NO_CHARGE
    assert result["reason"] is None
    assert result["candidates"] == []
    assert backend.calls[1] == (
        "coverage",
        {"start_date": date(2026, 8, 23), "end_date": AS_OF},
    )


@pytest.mark.parametrize("flag", ["true", 1, None, False])
def test_only_a_literal_true_flag_counts(flag: Any) -> None:
    backend = FakeBackend([SPROUTS], coverage_report(exhaustive=flag))
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_INDETERMINATE


def test_truncated_list_never_yields_a_negative() -> None:
    rows = [
        txn(f"t-{index}", "2026-09-01", "SPROUTS FARMERS MKT", 1.0)
        for index in range(3)
    ]
    backend = FakeBackend(rows, coverage_report(exhaustive=True))
    result = check_coverage(
        merchant_slug="costco-wholesale",
        on=ON,
        as_of=AS_OF,
        backend=backend,
        limit=3,
    )
    assert result["outcome"] == OUTCOME_INDETERMINATE
    assert result["reason"] == "list_truncated"
    assert [name for name, _ in backend.calls] == ["unmatched"]


def test_matching_row_with_bad_date_blocks_a_negative() -> None:
    bad = txn("t-bad", "not-a-date", "COSTCO WHSE #1234", 5.0)
    backend = FakeBackend([bad], coverage_report(exhaustive=True))
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_INDETERMINATE
    assert result["reason"] == "unparseable_row"


@pytest.mark.parametrize("description", ["", "   #1234", None, 42])
def test_row_without_a_readable_description_blocks_a_negative(
    description: Any,
) -> None:
    row = dict(SPROUTS, txn_id="t-blank", description=description)
    backend = FakeBackend([row], coverage_report(exhaustive=True))
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_INDETERMINATE
    assert result["reason"] == "unparseable_row"
    assert result["candidates"] == []


def test_a_real_candidate_still_wins_over_unreadable_rows() -> None:
    blank = dict(SPROUTS, txn_id="t-blank", description="")
    backend = FakeBackend([blank, COSTCO])
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_CANDIDATE
    assert [item["txn_id"] for item in result["candidates"]] == ["t-costco"]


def test_missing_backend_is_lookup_unavailable() -> None:
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=None
    )
    assert result["outcome"] == OUTCOME_UNAVAILABLE
    assert result["reason"] == "no_backend"
    assert result["candidates"] == []


def test_backend_exception_is_lookup_unavailable() -> None:
    backend = FakeBackend([], fail=RuntimeError("sqlite locked"))
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_UNAVAILABLE
    assert result["reason"] == "backend_error:RuntimeError"


def test_coverage_exception_is_lookup_unavailable() -> None:
    backend = FakeBackend([SPROUTS], fail_coverage=OSError("db gone"))
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_UNAVAILABLE
    assert result["reason"] == "backend_error:OSError"


def test_empty_window_is_indeterminate_without_calling_the_backend() -> None:
    backend = FakeBackend([COSTCO])
    result = check_coverage(
        merchant_slug="costco-wholesale",
        on=ON,
        as_of=date(2026, 8, 1),
        backend=backend,
    )
    assert result["outcome"] == OUTCOME_INDETERMINATE
    assert result["reason"] == "window_ends_before_it_starts"
    assert result["window"] == ["2026-08-23", "2026-08-01"]
    assert backend.calls == []


def test_result_never_carries_a_price() -> None:
    backend = FakeBackend([SPROUTS])
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert set(result) == {"outcome", "reason", "window", "candidates"}


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        check_coverage(merchant_slug="", on=ON, as_of=AS_OF, backend=None)
    with pytest.raises(ValueError):
        check_coverage(
            merchant_slug="costco-wholesale",
            on=ON,
            as_of=AS_OF,
            backend=None,
            limit=0,
        )
    with pytest.raises(ValueError):
        coverage_window(ON, AS_OF, -1)


def test_adapter_binds_the_backend_and_is_keyword_only() -> None:
    backend = FakeBackend([COSTCO])
    check = coverage_checker(backend, limit=25)
    result = check(merchant_slug="costco-wholesale", on=ON, as_of=AS_OF)
    assert result["outcome"] == OUTCOME_CANDIDATE
    assert backend.calls[0][1]["limit"] == 25
    with pytest.raises(TypeError):
        check("costco-wholesale", ON, AS_OF)  # type: ignore[misc]
    unavailable = coverage_checker(None)
    assert (
        unavailable(merchant_slug="costco-wholesale", on=ON, as_of=AS_OF)[
            "outcome"
        ]
        == OUTCOME_UNAVAILABLE
    )


FAKE_QUERIES = '''
def unmatched(conn, kind="txns", account=None, start_date=None,
              end_date=None, limit=50):
    rows = conn.execute(
        """SELECT txn_id, account, txn_date, description,
                  amount_cents / -100.0 AS amount, txn_class
           FROM chase_transactions
           WHERE txn_date >= ? AND txn_date <= ?
           ORDER BY txn_date DESC LIMIT ?""",
        (start_date, end_date, limit)).fetchall()
    return {"kind": kind, "count": len(rows), "rows": [dict(r) for r in rows]}


def coverage(conn, period="month", account=None, receiptable_only=False,
             start_date=None, end_date=None):
    return {"period": period, "receiptable_only": receiptable_only,
            "rows": [], "window": [start_date, end_date]}
'''


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A stand-in receipts-email checkout: ``emlrec`` package + SQLite db."""
    repo = tmp_path / "receipts-email"
    package = repo / "emlrec"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "db.py").write_text(
        f"DB_PATH = {str(repo / 'email_receipts.db')!r}\n"
    )
    (package / "queries.py").write_text(FAKE_QUERIES)
    conn = sqlite3.connect(repo / "email_receipts.db")
    conn.execute(
        "CREATE TABLE chase_transactions (txn_id TEXT, account TEXT,"
        " txn_date TEXT, description TEXT, amount_cents INTEGER,"
        " txn_class TEXT)"
    )
    conn.execute(
        "INSERT INTO chase_transactions VALUES"
        " ('t-costco', 'chase-sapphire', '2026-09-04', 'COSTCO WHSE #1234',"
        " -18742, 'in-person')"
    )
    conn.commit()
    conn.close()
    yield repo
    sys.modules.pop("emlrec", None)
    sys.modules.pop("emlrec.db", None)
    sys.modules.pop("emlrec.queries", None)
    if str(repo) in sys.path:
        sys.path.remove(str(repo))


def test_emlrec_backend_calls_the_repo_queries(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECEIPTS_EMAIL_REPO", str(fake_repo))
    monkeypatch.delenv("RECEIPTS_EMAIL_DB", raising=False)
    backend = EmlrecBackend()
    assert backend.db_path == str(fake_repo / "email_receipts.db")
    rows = backend.unmatched_transactions(
        start_date=date(2026, 8, 23), end_date=AS_OF, limit=10
    )
    assert rows == [
        {
            "txn_id": "t-costco",
            "account": "chase-sapphire",
            "txn_date": "2026-09-04",
            "description": "COSTCO WHSE #1234",
            "amount": 187.42,
            "txn_class": "in-person",
        }
    ]
    report = backend.coverage(start_date=date(2026, 8, 23), end_date=AS_OF)
    assert report["window"] == ["2026-08-23", "2026-09-09"]
    assert "exhaustive" not in report
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_CANDIDATE


def test_emlrec_backend_missing_repo_raises_import_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RECEIPTS_EMAIL_REPO", raising=False)
    with pytest.raises(ImportError):
        EmlrecBackend(repo_path=str(tmp_path / "nowhere"))


def test_emlrec_backend_missing_db_is_lookup_unavailable(
    fake_repo: Path,
) -> None:
    backend = EmlrecBackend(
        repo_path=str(fake_repo), db_path=str(fake_repo / "absent.db")
    )
    result = check_coverage(
        merchant_slug="costco-wholesale", on=ON, as_of=AS_OF, backend=backend
    )
    assert result["outcome"] == OUTCOME_UNAVAILABLE
    assert result["reason"] == "backend_error:FileNotFoundError"
    assert not (fake_repo / "absent.db").exists()


def test_default_checker_is_none_when_repo_missing(monkeypatch):
    from receipt_nutrition import coverage as module

    monkeypatch.setenv("RECEIPTS_EMAIL_REPO", "/does-not-exist")
    assert module.default_coverage_checker() is None
