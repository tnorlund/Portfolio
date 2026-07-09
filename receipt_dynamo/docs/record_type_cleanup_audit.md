# receipt_dynamo record type cleanup audit

Audit date: 2026-07-09.

## Evidence used

- Dev DynamoDB table: `ReceiptsTable-dc5be22` in `us-east-1`.
- Table scan: parallel scan of 1,997,238 items projecting only the `TYPE` attribute.
- Git history: `git log --diff-filter=A` for entity files to identify when record modules entered this repository history.
- Pylint: targeted run over `receipt_dynamo.entities` and `receipt_dynamo.data` with `unused-import`, `unused-variable`, `undefined-variable`, and `import-error` enabled.

## Removed records

These legacy record modules were removed because the dev table scan showed no live records of these `TYPE` values and the active replacement records are already present:

| Removed record | Dev count | Replacement / current model |
| --- | ---: | --- |
| `ReceiptChatGPTValidation` | 0 | `ReceiptValidationSummary`, `ReceiptValidationCategory`, and `ReceiptValidationResult` |
| `ReceiptField` | 0 | `ReceiptLabelAnalysis` and `ReceiptWordLabel` |
| `ReceiptMetadata` | 0 | `ReceiptPlace` |

The corresponding DynamoClient mixins, package exports, unit tests, and integration tests were removed with the record modules.

## Dev table counts for replacement records

| Record `TYPE` | Dev count |
| --- | ---: |
| `RECEIPT_LABEL_ANALYSIS` | 228 |
| `RECEIPT_PLACE` | 874 |
| `RECEIPT_VALIDATION_CATEGORY` | 1,368 |
| `RECEIPT_VALIDATION_RESULT` | 221 |
| `RECEIPT_VALIDATION_SUMMARY` | 228 |
| `RECEIPT_WORD_LABEL` | 61,211 |

## Notes

Several operational record types also had zero current rows in the dev table, but were not removed because they are ephemeral or still referenced by active runtime paths (for example compaction locks, queues, and job resources). This cleanup intentionally limits deletions to replaced receipt-analysis records.
