# TRANSACTION_INFO fresh-receipt shadow protocol

This protocol compares two immutable candidates without modifying either
checkout, the shared priors, receipt labels, or external data:

- frozen v4: `4190a878966784f0ccd2301669912e41d948dddc`;
- corrected: `099d1197aea778f32f547cac2fc42ad5f33c600e`; and
- shared priors SHA-256:
  `a10752fd037d9d1951b5195f06253e50b7116a3c39c7001b128802023ef613c4`.

## Input lock

The harness accepts exactly two external input files. It does not copy either
file into git.

The frozen preregistration is verified against SHA-256
`6957a10e004a9217425d266dd6e6f4e47d112f89b7f820df7665f49bf41d415d`
before either candidate can run.

`UPLOAD_MANIFEST.json` is supplied by Claude A. The harness accepts Claude
A's direct receipt list or the intake protocol's image list with nested
receipts; in either representation, cohort membership comes only from this
file:

```json
{
  "role": "claude_a_intake_audit",
  "protocol": "fresh_upload_v1",
  "t0_utc": "2026-07-17T21:07:13Z",
  "receipts": [
    {
      "image_id": "private source identifier",
      "receipt_id": 1,
      "merchant": null,
      "uploaded_at": "2026-07-17T14:10:00-07:00"
    }
  ]
}
```

The fixed intake baseline and every available receipt timestamp must be later
than corrected commit `099d1197a`. Receipt identity may appear only in these
private inputs and the process-local job; reports use a twelve-character salted
case digest. Images that failed before producing a receipt remain documented
in the intake manifest but do not create an evaluable receipt key.

`TRUTH_LOCKED.json` is supplied by Claude B only after labels are final:

```json
{
  "producer": "claude_b",
  "locked": true,
  "locked_at": "2026-07-17T14:30:00-07:00",
  "upload_manifest_sha256": "SHA-256 of exact UPLOAD_MANIFEST.json bytes",
  "receipts": [
    {
      "image_id": "same private source identifier",
      "receipt_id": 1,
      "rows": [
        {"row_id": 1, "section_type": "ITEMS"},
        {
          "row_id": 2,
          "section_type": "AMBIGUOUS",
          "note": "image and OCR evidence do not resolve this row"
        }
      ]
    }
  ]
}
```

The truth receipt set plus any explicitly reasoned, pre-run receipt exclusions
must equal the upload receipt set. `OTHER` is a scored truth class that neither
candidate can emit. `AMBIGUOUS` rows require a note, are excluded from every
metric, and are counted prominently. Duplicate receipts, duplicate truth rows,
a changed manifest hash, an unlocked file, an unexpected producer, or a
pre-freeze upload aborts before any decoder import or DynamoDB read.

## Isolation and read-only behavior

For each exact commit, the harness creates a temporary `git archive` and
launches a separate `python -I` process. It explicitly prepends only that
archive's `receipt_chroma`, `receipt_dynamo`, and `receipt_upload` roots, removes
foreign checkout paths, and rejects any loaded `receipt_*` module outside the
archive. Candidate workers share only third-party dependencies; they cannot
import another checkout's project packages.

The worker exposes no persistence adapter. It calls only the development
table's row and line read methods and rejects every other table. It does not
read QA sections from DynamoDB: the hash-bound truth lock is the sole scoring
source.

`RUN_STATE.json` is created exclusively before the first candidate invocation.
Any existing run state or output causes a hard refusal. Each candidate attempt
is recorded as one before launch; a failure is final and marked
`failed_no_rerun`.

## Preregistered reporting and recommendation boundary

The comparison reports:

- overall agreement, ITEMS recall, and TXINFO precision/recall with Wilson 95%
  confidence intervals;
- unassigned truth rows;
- dense confusion matrices including zero cells and unassigned predictions;
- all-row run counts, single-row runs, strict `A,B,A` islands, repeated section
  types, and type-contiguous receipt counts; and
- pseudonymized per-receipt accuracy, changed-row, and fragmentation deltas.

Frozen v4 is never promotion-eligible because its post-decoder mutation breaks
the authoritative-path invariant. The locked preregistration defines only four
numerical gates for each candidate:

1. overall agreement is at least 0.80;
2. ITEMS recall, TXINFO recall, and TXINFO precision are each at least 0.70;
3. TXINFO recall is at least 0.70; and
4. TXINFO precision is at least 0.70.

Wilson intervals, unassigned rows, and sequence-coherence measures are reported
but are not gates. Both candidate input-snapshot hashes must match or the
comparison is invalid. When fewer than 60 true TXINFO rows are available, the
TXINFO results are smoke evidence only and the correction remains draft even
if the four point gates pass. With at least 60 true TXINFO rows and all four
gates passing, the correction is eligible only for a separate promotion
review; it is not automatically merged or deployed. There is no tuning, rule
change, rerun, merge, or deployment after viewing results.
