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

`UPLOAD_MANIFEST.json` is supplied by Claude A:

```json
{
  "producer": "claude_a",
  "cohort": "fresh_upload_v1",
  "receipts": [
    {
      "image_id": "private source identifier",
      "receipt_id": 1,
      "merchant": null,
      "uploaded_at": "2026-07-17T14:00:00-07:00"
    }
  ]
}
```

Every `uploaded_at` must be later than corrected commit `099d1197a`. Receipt
identity may appear only in these private inputs and the process-local job;
reports use a twelve-character salted case digest.

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
        {"row_id": 1, "section_type": "ITEMS"}
      ]
    }
  ]
}
```

The truth receipt set must equal the upload receipt set. Duplicate receipts,
duplicate truth rows, a changed manifest hash, an unlocked file, an unexpected
producer, or a pre-freeze upload aborts before any decoder import or DynamoDB
read.

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
the authoritative-path invariant. The corrected candidate is only eligible
for a separate promotion review—not automatically merged or deployed—when all
of these precommitted checks pass:

1. overall agreement is at least 0.80;
2. ITEMS recall, TXINFO recall, and TXINFO precision are each at least 0.70;
3. TXINFO recall and precision Wilson lower bounds are each at least 0.70;
4. the cohort contains at least 60 TXINFO truth rows;
5. no scored row is unassigned;
6. both candidate input-snapshot hashes are identical; and
7. corrected strict islands and receipts with split section types do not exceed
   frozen v4.

A failed or statistically unresolved check keeps the correction draft. There
is no tuning, rule change, rerun, merge, or deployment after viewing results.
