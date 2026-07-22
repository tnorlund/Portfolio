"""Contract-checker unit tests (W2, plan humble-skipping-quilt): the 7
required sections, verdict-line grammar, ordering, and CLI exit codes.

Fixtures live in tests/fixtures/nightly/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.nightly import check_contract

FIXTURES = Path(__file__).parent / "fixtures" / "nightly"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


# --------------------------------------------------------------------------
# check_report_text
# --------------------------------------------------------------------------


def test_valid_report_passes():
    result = check_contract.check_report_text(load("valid_report.md"))
    assert result["valid"] is True
    assert result["verdict"] == "GREEN"
    assert result["problems"] == []


def test_missing_section_fails_and_names_it():
    result = check_contract.check_report_text(load("missing_section.md"))
    assert result["valid"] is False
    assert "missing section: ## Awaiting Owner" in result["problems"]


def test_no_verdict_fails():
    result = check_contract.check_report_text(load("no_verdict.md"))
    assert result["valid"] is False
    assert any("verdict" in p.lower() for p in result["problems"])
    assert result["verdict"] is None


def test_all_seven_sections_required_individually():
    text = load("valid_report.md")
    for section in check_contract.REQUIRED_SECTIONS:
        mutated = text.replace(f"## {section}\n", "## Something Else\n")
        result = check_contract.check_report_text(mutated)
        assert result["valid"] is False, section
        assert f"missing section: ## {section}" in result["problems"]


def test_out_of_order_sections_fail():
    text = load("valid_report.md")
    lines = text.splitlines(keepends=True)
    # Move the "## Tomorrow's Top 3" block before "## Verdict".
    idx = lines.index("## Tomorrow's Top 3\n")
    block, rest = lines[idx:], lines[:idx]
    verdict_idx = rest.index("## Verdict\n")
    mutated = "".join(rest[:verdict_idx] + block + ["\n"] + rest[verdict_idx:])
    result = check_contract.check_report_text(mutated)
    assert result["valid"] is False
    assert any("out of order" in p for p in result["problems"])


@pytest.mark.parametrize(
    "line,expected",
    [
        ("**Verdict: GREEN** - all good.", "GREEN"),
        ("**Verdict: YELLOW** - skips happened.", "YELLOW"),
        ("**Verdict: RED** - preflight red.", "RED"),
        ("Verdict: GREEN - bold optional.", "GREEN"),
        ("Verdict: RED: colon separator.", "RED"),
    ],
)
def test_verdict_grammar_accepts(line: str, expected: str):
    text = load("no_verdict.md").replace("All good tonight.", line)
    result = check_contract.check_report_text(text)
    assert result["valid"] is True
    assert result["verdict"] == expected


@pytest.mark.parametrize(
    "line",
    [
        "**Verdict: green** - lowercase color.",
        "**Verdict: GREEN**",  # no trailing sentence
        "**Verdict: BLUE** - unknown color.",
        "Verdict GREEN - missing colon.",
    ],
)
def test_verdict_grammar_rejects(line: str):
    text = load("no_verdict.md").replace("All good tonight.", line)
    result = check_contract.check_report_text(text)
    assert result["valid"] is False


def test_verdict_inside_code_fence_not_counted():
    result = check_contract.check_report_text(load("verdict_in_fence.md"))
    assert result["valid"] is False
    assert result["verdict"] is None
    assert any("no parseable verdict" in p for p in result["problems"])


def test_heading_inside_code_fence_not_counted():
    text = load("valid_report.md").replace(
        "## Awaiting Owner\nNone.\n",
        "```\n## Awaiting Owner\n```\nNone.\n",
    )
    result = check_contract.check_report_text(text)
    assert result["valid"] is False
    assert "missing section: ## Awaiting Owner" in result["problems"]


def test_empty_section_body_fails():
    result = check_contract.check_report_text(load("empty_section.md"))
    assert result["valid"] is False
    assert "empty section: ## Awaiting Owner" in result["problems"]


def test_section_body_of_only_a_fence_counts_as_empty():
    text = load("valid_report.md").replace(
        "## Awaiting Owner\nNone.\n",
        "## Awaiting Owner\n```\nquoted stuff\n```\n",
    )
    result = check_contract.check_report_text(text)
    assert result["valid"] is False
    assert "empty section: ## Awaiting Owner" in result["problems"]


def test_multiple_verdict_lines_rejected():
    text = load("valid_report.md") + "\n**Verdict: RED** - second verdict.\n"
    result = check_contract.check_report_text(text)
    assert result["valid"] is False
    assert any("multiple verdict lines" in p for p in result["problems"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_exit_codes(capsys):
    assert check_contract.main([str(FIXTURES / "valid_report.md")]) == 0
    assert check_contract.main([str(FIXTURES / "missing_section.md")]) == 1
    assert check_contract.main([str(FIXTURES / "no_verdict.md")]) == 1
    assert check_contract.main([str(FIXTURES / "does_not_exist.md")]) == 2


def test_cli_verdict_flag(capsys):
    rc = check_contract.main(["--verdict", str(FIXTURES / "valid_report.md")])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "GREEN"


def test_cli_json_flag(capsys):
    import json

    rc = check_contract.main(["--json", str(FIXTURES / "valid_report.md")])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["valid"] is True
    assert doc["verdict"] == "GREEN"
