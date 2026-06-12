# Canonical Fields Deprecation

Canonical receipt metadata fields were introduced for older harmonization flows
that copied one representative merchant, address, phone, and place ID across a
cluster of receipts. That bulk harmonizer is retired. New code should treat the
base receipt place fields as the source of truth and should correct them through
the current place workflows.

## Fields

- `canonical_merchant_name`
- `canonical_address`
- `canonical_phone_number`
- `canonical_place_id`

## Current Source Of Truth

- Missing place IDs are filled by `receipt_agent.agents.place_id_finder`.
- Incorrect `ReceiptPlace` records are corrected through the fix-place workflow.
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
