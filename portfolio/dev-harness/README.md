# Local validation review harness

Dev-only workstation for reviewing line-item extraction one merchant at a
time. Nothing here ships: `next.config.js` only wires `/api/validation/*` in
`PHASE_DEVELOPMENT_SERVER`, and no Lambda imports the shim.

```
.dev-harness/                 (gitignored, agent ↔ reviewer handoff)
├── queues/<name>.json        ordered receipt list for one session
├── dossiers/<image>-<n>.json per-receipt analysis + proposed fix
└── review_log.jsonl          the reviewer's verdicts (the only file written)
```

## Running

```bash
python portfolio/dev-harness/validation_shim.py --port 8787   # terminal 1
cd portfolio && npm run dev                                    # terminal 2
open http://localhost:3000/dev/validation
```

| Environment              | Default                          |
| ------------------------ | -------------------------------- |
| `DYNAMODB_TABLE_NAME`    | `ReceiptsTable-dc5be22` (dev)    |
| `VALIDATION_HARNESS_DIR` | `<repo>/.dev-harness`            |
| `VALIDATION_REVIEW_LOG`  | `<harness dir>/review_log.jsonl` |

Routes: `/merchants`, `/queues`, `/worklist`, `/receipt`, `/review`,
`/line_item_decode`, `/health`.

## Curated queues

`GET /worklist?queue=<name>` reads `.dev-harness/queues/<name>.json` and
returns those receipts **in file order**, ignoring the merchant and status
filters — a queue is a session's agenda, not another filter. Ids the index
does not contain come back in `missing` rather than being dropped silently.

```json
{
  "description": "Smith's + Gelson's, all statuses",
  "receipts": [
    { "image_id": "0f7c…", "receipt_id": 1 },
    { "image_id": "3a21…", "receipt_id": 2 }
  ]
}
```

A bare JSON array of the same objects works too.

## Dossiers

`GET /receipt` attaches `.dev-harness/dossiers/<image_id>-<receipt_id>.json`
as `dossier`; a file that cannot be parsed comes back as `dossier_error` so a
broken dossier never looks like an absent one.

```json
{
  "failure_mode": "H-zone-gap-missing-items",
  "diagnosis": "The ITEMS section stops four rows above the subtotal.",
  "evidence": ["bands 18-21 are priced rows no item claimed"],
  "proposal": {
    "tool": "extend_items_section",
    "args": {
      "image_id": "0f7c…",
      "receipt_id": 1,
      "line_ids": [18, 19, 20, 21]
    },
    "dry_run": {
      "before_delta": -4.18,
      "after_delta": 0.0,
      "before_status": "mismatch",
      "after_status": "match"
    }
  },
  "abstain_reason": null,
  "author": "scout",
  "generated_at": "2026-08-03T12:00:00Z"
}
```

Set `proposal` to `null` and give an `abstain_reason` when the fix does not
strictly improve both delta and status. The UI renders the abstention as-is;
an honest abstention is more useful than a guess, and **Approve fix** stays
disabled without a proposal.

## Verdicts

| Verdict       | Means                                               | Consumed by                                           |
| ------------- | --------------------------------------------------- | ----------------------------------------------------- |
| `confirm`     | The reviewer agrees with what the pipeline produced | golden promotion when the receipt is also bank-proven |
| `flag`        | Something is wrong; note + `reason` say what        | issue filing                                          |
| `approve-fix` | Run the dossier's proposal for real                 | the post-session writer agent                         |
| `golden`      | Promote into the bank-proven fixture set            | the scribe                                            |

Entries carry an optional `reason` (the A–J failure-mode code) and `line_ids`
(which rows the verdict is about). **Golden** is enabled only for a receipt
that reconciles _and_ whose printed total the bank settled; **Approve fix**
only when the dossier carries a proposal.

A `flag` on a green row is a stop-everything signal: it means the tolerance
ladder is admitting false accepts, and every count above it is suspect.

## Post-session sync

`.dev-harness/` is gitignored, so a session's verdicts live on one laptop
until they are synced. After a session:

```bash
portfolio/dev-harness/sync_reviews.sh          # → docs/line-items/reviews/<today>.jsonl
portfolio/dev-harness/sync_reviews.sh 2026-08-03
```

The script appends only entries not already present (matched on
`image_id`/`receipt_id`/`ts`), so re-running it mid-session is safe. Commit
the resulting file: it is the seed corpus for the bank-proven golden loop and
the only durable record of what a human actually judged.
