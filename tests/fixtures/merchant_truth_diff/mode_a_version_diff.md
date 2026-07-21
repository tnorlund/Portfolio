# Merchant-truth diff: vons v1 -> v2

Table: `ReceiptsTable-dc5be22`

- bundle_hash: `b9aa8cab58e6fb0ec67fbd497626a395b9c6657b33fa6255e1f7724de9dacdb8` ->
  `5862c420875ffe385ae3f7c1625d4bfe82cb2084b9809d76916adce19d9d873d`
- gate: PASS -> PASS | sealed_at: 2026-07-21T05:00:00+00:00 -> 2026-07-22T05:00:00+00:00

## Components

- identity: identical (content_hash `34a3b352fe75`)
- stylemap: identical (content_hash `68095ec717bc`)

### typography — CHANGED (`ec190e78a501` -> `a76daf97b9b4`)

- typography.condense: 0.93 -> 0.95
- typography.ink (added): 0.82
  provenance pipeline: "merchant_truth_v1@1" -> "glyphstudio.layout_template@1"
  provenance git_sha: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" -> "feedbeef00112233445566778899aabbccddeeff"
  provenance measured_at: null -> "2026-07-22T04:00:00+00:00"

### layout — CHANGED (`bdc1c2034dd8` -> `7ad5b76b8605`)

- footer: desc/left column added at x 0.0171 (spread 0.0186, support 8)
- items: amount/right column x 0.9439 -> 0.9502 (moved +0.0063 paper-width)
- items: amount/right column support: 8 -> 11
- payment: label/left column removed at x 0.0063 (spread 0.0124, support 8)
- template.measured.receipts: 12 -> 15
- template.measured.tool_git_sha: "aaaa11112222" -> "bbbb33334444"
  provenance pipeline: "merchant_truth_v1@1" -> "glyphstudio.layout_template@1"
  provenance git_sha: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" -> "feedbeef00112233445566778899aabbccddeeff"
  provenance measured_at: null -> "2026-07-22T04:00:00+00:00"

### assets — CHANGED (`8a5ddebe9602` -> `1692b3841032`)

- font[regular] hash: "1111111111111111111111111111111111111111111111111111111111111111" -> "3333333333333333333333333333333333333333333333333333333333333333"
- font[regular] pointer: "fonts/vons/vons-1111.glyphs.npz" -> "fonts/vons/vons-3333.glyphs.npz"
- font[regular].compiled_at: "2026-07-01T00:00:00+00:00" -> "2026-07-22T00:00:00+00:00"
- font[regular].source_commit: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" -> "feedbeef00112233445566778899aabbccddeeff"
  provenance pipeline: "merchant_truth_v1@1" -> "glyphstudio.layout_template@1"
  provenance git_sha: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" -> "feedbeef00112233445566778899aabbccddeeff"
  provenance measured_at: null -> "2026-07-22T04:00:00+00:00"

### flags — CHANGED (`c2c16d31005b` -> `b5152efc3a5f`)

- typography.reverse_total: true -> false
  provenance pipeline: "merchant_truth_v1@1" -> "glyphstudio.layout_template@1"
  provenance git_sha: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" -> "feedbeef00112233445566778899aabbccddeeff"
  provenance measured_at: null -> "2026-07-22T04:00:00+00:00"

### catalog_snapshot — CHANGED (`add723d20556` -> `90ee0edf45ae`)

- price changed: dairy/MILK 2% "3.99" -> "4.19"
- item removed: produce/FUJI APPLES (price "2.49")
- item added: produce/ORANGES (price "3.49")
- catalog_hash: "7d3929ba57911730d1e60f3f0ce81d02f5d0c82f7cfe8426469e98bea4d1f4b6" -> "018be2b531a6a604baea56787a7d6c23b9af4df5ad1385ddac7050c548cc1ff9"
- as_of: "2026-07-20T00:00:00+00:00" -> "2026-07-22T00:00:00+00:00"
  provenance pipeline: "merchant_truth_v1@1" -> "glyphstudio.layout_template@1"
  provenance git_sha: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" -> "feedbeef00112233445566778899aabbccddeeff"
  provenance measured_at: null -> "2026-07-22T04:00:00+00:00"
