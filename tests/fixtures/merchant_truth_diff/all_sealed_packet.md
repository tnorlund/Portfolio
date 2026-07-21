# Merchant-truth G2 review packet: SEALED pending activation

Table: `ReceiptsTable-dc5be22` | 1 sealed version(s) awaiting an ACTIVE flip

---

# Merchant-truth activation review: vons v2

Table: `ReceiptsTable-dc5be22`

UPGRADE: ACTIVE currently points at v1 (`b9aa8cab58e6`); flipping moves it to v2.

## Decision summary

- bundle_hash: `5862c420875ffe385ae3f7c1625d4bfe82cb2084b9809d76916adce19d9d873d`
- manifest: SEALED, sealed_at 2026-07-22T05:00:00+00:00
- gate_status: PASS — full_fidelity_eval (fidelity-eval)
  - note: metrics PASS on truth-loaded renders
  - evidence: git_sha="feedbeef00112233445566778899aabbccddeeff", report="docs/reports/nightly/2026-07-22.md"
  - gate written_by: measurement_pipeline/glyphstudio@1
- mint: run `merchant-truth-v2-vons-feedbeef0011`, written_by measurement_pipeline/glyphstudio@1, git_sha "feedbeef00112233445566778899aabbccddeeff"

## Components

| component | content_hash | payload bytes |
|---|---|---|
| identity | `34a3b352fe75` | 125 |
| typography | `a76daf97b9b4` | 136 |
| stylemap | `68095ec717bc` | 292 |
| layout | `7ad5b76b8605` | 454 |
| assets | `1692b3841032` | 730 |
| flags | `b5152efc3a5f` | 108 |
| catalog_snapshot | `90ee0edf45ae` | 331 |

## Key measured values

### identity

- merchant_name: "Vons"
- slug: vons (upper VONS)
- normalized aliases: 2

### typography

- section_scale.FOOTER: 0.5
- typography.bitmap_font.regular: "vons.glyphs.npz"
- typography.bitmap_thin: 0.0
- typography.condense: 0.95
- typography.ink: 0.82

### layout

- available: true
- measured: 15 receipts (tool bbbb33334444, dirty false)
- columns per section: footer=1, items=2, payment=1

### assets

- font[regular]: fonts/vons/vons-3333.glyphs.npz hash `333333333333`
- logo: fonts/vons/logo-2222.png hash `222222222222`
- profile assets: logo="vons_logo.png", logo_anchor={"center":true,"phrases":["VONS"]}, stylemap_filename="vons_stylemap.json"

### stylemap

- available: true
- source: fonts/vons/stylemap-5f5f.json hash `5f5f5f5f5f5f`
- document sections: 1

### catalog_snapshot

- items: 3 (catalog_hash `018be2b531a6`, as_of 2026-07-22T00:00:00+00:00)

### flags

- 4 decided leaves across groups: header, typography
- flags are git-sourced engine config (truth = measured; flags = decided)

## Flip

This tool never flips. The owner runs the flip (DynamoClient.initial_activate for a first activation, flip_active thereafter) after reading this review — owner gate G2.
