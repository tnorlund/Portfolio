# Canonical Fields Deprecation

Canonical receipt metadata fields were introduced for older harmonization flows
that copied one representative merchant, address, phone, and place ID across a
cluster of receipts. That bulk harmonizer is retired. New code should treat the
base receipt place fields as the source of truth and should correct them through
the current place workflows.

## Entity Source Of Truth

`RECEIPT_PLACE` is the source of truth for a receipt's merchant and location.
Active merchant lookup, census, rendering, and analytics code must use
`ReceiptPlace` data:

- Use `get_receipt_places_by_merchant` for merchant-specific lookups.
- For a table-wide census, query the `GSITYPE` index with
  `TYPE = RECEIPT_PLACE` and parse the scalar `merchant_name` field.
- Do not add new reads of `RECEIPT_METADATA` or `ReceiptMetadata` for current
  merchant state.

`RECEIPT_METADATA` is legacy. Its model and data APIs remain only for workflows
that must preserve or transform historical rows: the dev-to-prod mirror,
reconciliation, cleanup/repair, and the metadata-to-place backfill. Those
exceptions do not make metadata authoritative.

### Drifted Legacy Rows

Historical `RECEIPT_METADATA` rows can contain stale secondary-index values
derived from an older merchant name, place ID, or validation status. The legacy
converter tolerates those drifted GSI projections so a migration or backfill
page does not crash, while still validating primary identity (`PK`, `SK`, and
`TYPE`) and field shapes. Consumers must not interpret a tolerated metadata row
as fresher than its corresponding `RECEIPT_PLACE` row.

`RECEIPT_PLACE` rows can also retain old secondary-index projections after a
scalar repair. This is why table-wide census tooling uses the raw `GSITYPE`
query described above instead of requiring every historical row to satisfy the
current entity converter.

## Fields

- `canonical_merchant_name`
- `canonical_address`
- `canonical_phone_number`
- `canonical_place_id`

## Current Source Of Truth

- Missing place IDs are filled by `receipt_agent.agents.place_id_finder`.
- Incorrect `RECEIPT_PLACE` records are corrected through the fix-place
  workflow.
- Corrections use receipt text, Google Places, ChromaDB context, and explicit MCP
  update tools.
- Label quality and financial consistency are validated by the label evaluator
  and its viz-cache output.

## Migration Guidance

Do not add new consumers of canonical fields. Existing consumers should migrate
to base receipt place fields:

- `merchant_name`
- `formatted_address` or the current address field used by `ReceiptPlace`
- `phone_number`
- `place_id`

Before removing the canonical fields from DynamoDB entities, audit remaining
references and confirm that any historical canonical-only data is no longer
needed.

## Remaining Work

1. Search for remaining `canonical_*` consumers.
2. Move any active reads to the base receipt place fields.
3. Update tests and serialization once no active consumers remain.
4. Remove canonical fields from the entity model in a separate schema cleanup PR.
