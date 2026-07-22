# Nightly Report {{DATE}} (dry-run canned output)

This report was produced by `run_nightly.sh --dry-run`: the headless claude
call was skipped and this canned agent output was substituted. It exists to
exercise the wrapper end-to-end (preflight -> contract check -> publish)
without spending an agent run.

## Verdict
**Verdict: YELLOW** - dry-run: wrapper path exercised end-to-end, no real eval work performed.

## Budget
- wall clock: 0s agent time (claude call skipped by --dry-run)
- turns: 0 of 80
- subagents: 0 of 3

## Fleet
Fleet table not collected (dry-run substitutes canned output for the agent).
Delta vs yesterday: n/a (dry-run).

| slug | version | gate | staleness |
|------|---------|------|-----------|
| (dry-run placeholder - real runs paste fleet_status markdown here) | - | - | - |

## Tonight's Work
- dry-run: SKIPPED - all 3 merchant evals skipped because --dry-run never
  launches the agent.

## Awaiting Owner
None (dry-run).

## Failures & Anomalies
None (dry-run).

## Tomorrow's Top 3
1. Run a real supervised night (owner-gated first run) - driving item: W2 acceptance
2. Verify preflight is GREEN on the mini (receipt-tools MCP auth) - driving item: W1 op
3. Confirm nightly/YYYY-MM-DD branch publishing from the mini - driving item: W2 publish leg
