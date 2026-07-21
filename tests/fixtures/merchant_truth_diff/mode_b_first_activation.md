# Merchant-truth activation review: vons v1

Table: `ReceiptsTable-dc5be22`

FIRST ACTIVATION: no ACTIVE pointer exists for this merchant. Flipping creates the initial pointer (conditional Put; owner gate G2).

## Decision summary

- bundle_hash: `b9aa8cab58e6fb0ec67fbd497626a395b9c6657b33fa6255e1f7724de9dacdb8`
- manifest: SEALED, sealed_at 2026-07-21T05:00:00+00:00
- gate_status: PASS — merchant_truth_v1_bootstrap (migration-bootstrap-seal)
  - note: bootstrap seal: gate passes on dry-run<->live source parity only; no fidelity eval has run against truth-loaded renders yet
  - evidence: generated_at="2026-07-21T04:00:00+00:00", git_sha="1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
  - gate written_by: migration/merchant_truth_v1@1
- mint: run `merchant-truth-v1-vons-1a2b3c4d5e6f`, written_by migration/merchant_truth_v1@1, git_sha "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"

## Components

| component | content_hash | payload bytes |
|---|---|---|
| identity | `34a3b352fe75` | 125 |
| typography | `ec190e78a501` | 125 |
| stylemap | `68095ec717bc` | 292 |
| layout | `bdc1c2034dd8` | 443 |
| assets | `8a5ddebe9602` | 730 |
| flags | `c2c16d31005b` | 107 |
| catalog_snapshot | `add723d20556` | 335 |

## Key measured values

### identity

- merchant_name: "Vons"
- slug: vons (upper VONS)
- normalized aliases: 2

### typography

- section_scale.FOOTER: 0.5
- typography.bitmap_font.regular: "vons.glyphs.npz"
- typography.bitmap_thin: 0.0
- typography.condense: 0.93

### layout

- available: true
- measured: 12 receipts (tool aaaa11112222, dirty false)
- columns per section: items=2, payment=2

### assets

- font[regular]: fonts/vons/vons-1111.glyphs.npz hash `111111111111`
- logo: fonts/vons/logo-2222.png hash `222222222222`
- profile assets: logo="vons_logo.png", logo_anchor={"center":true,"phrases":["VONS"]}, stylemap_filename="vons_stylemap.json"

### stylemap

- available: true
- source: fonts/vons/stylemap-5f5f.json hash `5f5f5f5f5f5f`
- document sections: 1

### catalog_snapshot

- items: 3 (catalog_hash `7d3929ba5791`, as_of 2026-07-20T00:00:00+00:00)

### flags

- 4 decided leaves across groups: header, typography
- flags are git-sourced engine config (truth = measured; flags = decided)

## Flip

This tool never flips. The owner runs the flip (DynamoClient.initial_activate for a first activation, flip_active thereafter) after reading this review — owner gate G2.
