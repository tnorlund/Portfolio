# Automated review loop — email receipt inbox (2026-07-18)

Artifacts from the Codex/Claude adversarial review loop run against PR #1175
(`infra/email_receipt_inbox`). The loop ran on the Mac mini out of
`~/portfolio_review_loop`, which is a scratch checkout, not a clone that ever
gets pushed — these files existed only on that machine until this commit.

Two runs are preserved:

- `run1-2026-07-18-1129/` — six review rounds, 11:29–12:21.
- `run2-2026-07-18-1224/` — three further rounds after the run-1 verdict,
  12:24–12:45. This is the run that produced the final verdict.

Each round is a set of four files sharing a round number: `bundle_N.md` is the
diff + context handed to the reviewer, `review_N.md` is the findings it
returned, `resolution_N.md` is what was changed in response, and
`verify_N.log` records the verification command result. `HUMAN.md` is the
running human-readable digest, `timeline.log` the round timings, and `gh.log`
the PR comments the loop posted.

`verdict.md` holds the single remaining medium-severity finding at cap: the
`email_receipt_inbox` handler persists unexpected parser exceptions
(`TypeError`, `AttributeError`) as successful `parse_error` results, so they
bypass retries and the DLQ instead of propagating.

The raw `codex_err_*.log` and `verdict_err.log` stderr streams (~3 MB of tool
transcript) are deliberately not committed; everything else from both run
directories is here verbatim.
