# Legacy comparison: vons v1 vs merchant_profiles.json

Table: `ReceiptsTable-dc5be22` | Legacy profile: "Vons"

| component | crosswalk coverage | result |
|---|---|---|
| identity | full (profile-derived) | BYTE-EQUIVALENT (`34a3b352fe75`) |
| typography | full (profile-derived) | BYTE-EQUIVALENT (`ec190e78a501`) |
| stylemap | none — sourced from MerchantFont/S3 stylemap object, not merchant_profiles.json | SKIPPED |
| layout | full (profile-derived) | BYTE-EQUIVALENT (`bdc1c2034dd8`) |
| assets | partial: profile sub-object only | BYTE-EQUIVALENT (`f761ba880548`) |
| flags | full (profile-derived) | BYTE-EQUIVALENT (`c2c16d31005b`) |
| catalog_snapshot | none — sourced from the MERCHANT_CATALOG partition, not merchant_profiles.json | SKIPPED |

All profile-derived content is byte-equivalent to the sealed bundle (expected for G1 v1 bundles minted from this file).
