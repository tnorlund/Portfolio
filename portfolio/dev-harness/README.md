# Local validation review harness

Dev-only workstation for the human half of the agentic review loop. The agent
adjudicates the corpus; the reviewer signs off on batches, blind-audits what
the agent applied unsupervised, and settles escalations. Nothing here ships:
`next.config.js` only wires `/api/validation/*` in `PHASE_DEVELOPMENT_SERVER`,
and no Lambda imports the shim.

```
.dev-harness/                 (gitignored, agent ↔ reviewer handoff)
├── queues/<name>.json        T2 escalation agenda, in review order
├── dossiers/<image>-<n>.json per-receipt analysis + proposed fix
├── verdicts/<pass-id>.jsonl       adjudicated verdicts, one per line  (read)
├── verdicts/<pass-id>.digest.json the grouped T1 view                 (read)
├── approvals/<pass-id>.json       T1 groups the human approved     (written)
├── freeze/<tier-or-class>         a failed blind audit             (written)
└── review_log.jsonl               the reviewer's verdicts          (written)
```

## Three screens

| Screen         | Reads                    | Writes                     |
| -------------- | ------------------------ | -------------------------- |
| **Digest**     | `/digest` (T1 groups)    | `/approve` → `approvals/`  |
| **Audit**      | `/audit` (blind sample)  | `/review` → `freeze/`, log |
| **Escalation** | `/queues` + `/worklist`  | `/review` → log            |

There is no merchant browser and no status filters: anything the agent could
decide for itself never reaches a screen, so the only orderings that exist are
the ones the adjudicator wrote.

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

Routes: `/merchants`, `/queues`, `/worklist`, `/receipt`, `/digest`,
`/verdicts`, `/audit`, `/review`, `/approve`, `/line_item_decode`, `/health`.

## Escalation queues

`GET /worklist?queue=<name>` reads `.dev-harness/queues/<name>.json` and
returns those receipts **in file order** — a queue is a session's agenda, not
another filter. Ids the index does not contain come back in `missing` rather
than being dropped silently.

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

## Adjudicated passes

`scripts/agentic_adjudicate.py --pass-id <id>` writes
`verdicts/<id>.jsonl` plus `verdicts/<id>.digest.json`; the newest pass by
mtime is the default everywhere. A `verdicts/<id>/` directory holding
`verdicts.jsonl` + `digest.json` is read too. Each verdict line routes one
receipt — the fields this shim reads are:

```json
{
  "image_id": "0f7c…", "receipt_id": 1, "merchant": "Smith's",
  "mode": "H-zone-gap", "tier": "T1", "reason": "guarded-extension",
  "golden": false, "group_id": "smith-s::h-zone-gap",
  "proposal": {"add_line_ids": [18, 19], "verified": true,
               "before": {"delta": -4.18}, "after": {"delta": 0.0}}
}
```

`tier` is `T0` (applied unsupervised — the audit pool), `T1` (batched for
sign-off), `T2` (escalated), or `abstain`. The money lives on the proposal's
before/after, not in a `delta` field, so `/digest` always reads the verdicts
file even when a `digest.json` exists — the digest names membership, the
verdicts name the gap the group would close. Without a `digest.json` the
shim groups the `T1` rows itself, using the adjudicator's own `group_id`
slugging so the ids match either way.

`POST /approve {pass_id, group_id}` appends the id to **`approved_groups`**
in `approvals/<pass-id>.json` — the exact key `agentic_writer.py` reads. Any
hand-written `t2_retirements` in that file survives, and the richer record
(receipts, action, who and when) goes to `approval_log`, which the writer
ignores.

## Blind audit and the freeze

`GET /audit` samples 10% of a pass's `T0` verdicts (minimum 3), seeded on the
pass id so the deck is stable across reloads and reproducible later.
`GET /audit?image_id=…&receipt_id=…` serves that receipt with the agent's
*conclusions* stripped — mode, diagnosis, proposal, confidence and
recommendation are all withheld; only its raw observations (`evidence` +
`visual_evidence`) survive. The audit is worth nothing if the human can read
the answer first.

Committing `audit-disagree` writes **two** markers, per the operating model's
`freeze/<tier-or-class>`: the audited verdict's tier (`T0`) and its A–J class
letter (`H`). Those are the only names `agentic_adjudicate.load_frozen`
matches — a marker named for the full mode id would be a freeze that silently
does nothing. The class letter comes from the adjudicated verdict rather than
the dossier, because that is the string the next pass will classify. A frozen
class cannot be approved on the digest, is flagged in `/verdicts`, and is
demoted to T2 by every later adjudication run until the marker is deleted by
hand. One disagreement is enough: the sample exists to catch a systematic
error, and a systematic error does not need a second data point.

## Verdicts

| Verdict          | Means                                               | Consumed by                                           |
| ---------------- | --------------------------------------------------- | ----------------------------------------------------- |
| `confirm`        | The reviewer agrees with what the pipeline produced | golden promotion when the receipt is also bank-proven |
| `flag`           | Something is wrong; note + `reason` say what        | issue filing                                          |
| `approve-fix`    | Run the dossier's proposal for real                 | the post-session writer agent                         |
| `golden`         | Promote into the bank-proven fixture set            | the scribe                                            |
| `audit-agree`    | Blind review reached the agent's conclusion         | the loop's accuracy record                            |
| `audit-disagree` | Blind review contradicted it — freezes the class    | the adjudicator and the writer                        |

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
