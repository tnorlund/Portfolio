# infra/ops — host-level operational units

launchd units for the mini (loop host). These files are COMMITTED here but
NOT installed by any PR or CI — installation is an owner op.

## com.tnorlund.receipt-nightly

Nightly receipt-synthesis loop v0 (02:00 daily, caffeinate-wrapped,
`RunAtLoad` false). Wrapper: `scripts/nightly/run_nightly.sh`; mission:
`docs/nightly/BRIEF.md`; report contract: `docs/nightly/REPORT_CONTRACT.md`.

Install (owner op, on the mini):

```sh
launchctl bootstrap gui/$UID /Users/tnorlund/Portfolio/infra/ops/com.tnorlund.receipt-nightly.plist
```

Uninstall / trigger-now / status:

```sh
launchctl bootout gui/$UID/com.tnorlund.receipt-nightly
launchctl kickstart gui/$UID/com.tnorlund.receipt-nightly
launchctl print gui/$UID/com.tnorlund.receipt-nightly | head -30
```

Logs: `~/Library/Logs/receipt-nightly.{out,err}.log` plus per-run dirs in
`~/.nightly-loop/YYYY-MM-DD/`. Reports land on `nightly/YYYY-MM-DD`
branches (never merged to main) and at `docs/reports/nightly/` in the loop
checkout.
