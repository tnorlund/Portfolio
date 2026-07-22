# Nightly Morning-Report Contract (v0)

Every nightly run MUST end with one report file at
`docs/reports/nightly/YYYY-MM-DD.md` (the wrapper exports the exact path as
`$NIGHTLY_REPORT_PATH`). The wrapper validates the report with
`scripts/nightly/check_contract.py`; a missing or invalid report is replaced
by a RED stub. This file is the single source of truth for what "valid"
means.

## Required sections

All seven `##` headings below must be present, in this order. Heading match
is case-insensitive on the title text but must be a level-2 markdown heading
(`## `). Extra sections are allowed after the required ones; extra prose
inside sections is allowed.

Two hardening rules:

- Fenced code blocks (``` or ~~~) are stripped before scanning: a heading
  or verdict line quoted inside a fence does not count.
- Every required section needs a non-empty body: at least one
  non-whitespace line (outside fences) before the next `#`/`##` heading.
  Write `None.` rather than leaving a section blank.

### 1. `## Verdict`

Must contain exactly one verdict line matching this grammar (first match
wins; the checker rejects reports with zero matches):

```
**Verdict: GREEN** - <one sentence>
```

- Color is one of `GREEN`, `YELLOW`, `RED` (uppercase).
- The `**` bold markers are optional; the ` - ` separator may also be `: `.
- The trailing sentence is required (why this color, one line).

Meaning: `GREEN` = everything planned ran and passed; `YELLOW` = ran with
skips/degradations worth reading; `RED` = the night did not produce usable
work (preflight RED, watchdog kill, contract failure).

### 2. `## Budget`

Wall clock used, agent turns used (vs `--max-turns`), subagent count
(vs the <=3 cap). A stub report states `n/a` where unknown.

### 3. `## Fleet`

The `fleet_status` markdown table (ACTIVE rows, sealed-pending, missing
merchants) plus a one-line delta vs yesterday's report (or "no prior
report" / "fleet unavailable" in a stub).

### 4. `## Tonight's Work`

Per merchant touched tonight: what ran and the result. For loop v0
(eval-only) that is the full-fidelity eval verdict per metric, or
`SKIPPED` with the reason. Later loop versions add minted
version/hash, metric red->green deltas, evidence-sheet paths+SHAs, and the
diff artifact here.

### 5. `## Awaiting Owner`

Everything that needs a human decision: staged flip commands each with a
one-line diff summary (v1+; v0 stages nothing), merge FYIs and exceptions,
PARKED items with the single decision needed. `None` is a valid body.

### 6. `## Failures & Anomalies`

Preflight degradations (e.g. receipt-tools MCP unauthenticated), contract
or watchdog events, reaped screens, eval errors, anything surprising.
`None` is a valid body.

### 7. `## Tomorrow's Top 3`

Three (or fewer, with a reason) concrete targets, each with the driving
metric or proposal.

## Checker behavior

`scripts/nightly/check_contract.py REPORT.md`:

- exit 0: all seven sections present in order + parseable verdict line.
- exit 1: invalid (each problem printed, one per line).
- exit 2: file missing/unreadable.
- `--verdict` prints just the parsed color (GREEN/YELLOW/RED) for a valid
  report; `--json` emits the machine-readable result.

## Delivery

The wrapper commits the report to a `nightly/YYYY-MM-DD` branch pushed to
origin (never merged to main) and appends a one-line summary to
`~/section_label_kickoff/CAMPAIGN_LOG.md`. A condensed comment on the
pinned fleet issue is a later addition (v1).
