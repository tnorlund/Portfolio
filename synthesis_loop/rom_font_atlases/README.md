# ROM font atlases (chart-derived, single-copy — deliberately IN git)

Compiled `.glyphs.npz` normally stay out of git because they are reproducible
from the skeleton sources in `tools/glyph-studio/fonts/`. The atlases here are
NOT: they were extracted from receiptfont.com specimen charts with
`synthesis_loop/extract_bitmatrix.py`, and several of the source charts no
longer exist on any machine (lost in a disk cleanup that also deleted the
`/tmp/bitmatrix` staging on the laptop). Each file below is (or was) the only
remaining copy, so it is vaulted here until published to S3/Dynamo via
`synthesis_loop/publish_merchant_font.py`.

| file | merchant (attribution) | provenance |
|---|---|---|
| `bitMatrix-B2.glyphs.npz` | Dollar Tree (raw ROM) | extracted from chart `626436-bitMatrix-B2.png` (chart LOST); manifest `../rom_font_manifests/bitMatrix-B2.manifest.json`; validated in `../rom_font_manifests/validation_results.json` (B2 wins DT, median IoU 0.5283) |
| `dollartree.glyphs.npz` | Dollar Tree (publish target) | `build_dollartree_font.py` double-strike of the raw B2; byte-verified: re-applying the 1px right+down double-strike to `bitMatrix-B2.glyphs.npz` reproduces this file exactly (all 188 arrays) |
| `pixCrog.glyphs.npz` | Smith's (ROM winner, median IoU 0.4713) | extracted from receiptfont.com pixCrog chart (chart LOST); manifest `../rom_font_manifests/pixCrog.manifest.json` |
| `bitMatrix-A2.glyphs.npz` | Amazon Fresh (receiptfont.com family attribution, shared w/ Whole Foods) | extracted from chart `626436-bitMatrix-A2.png` (chart survives on the mini at `~/rom_fonts_new/charts/`); manifest `../rom_font_manifests/bitMatrix-A2.manifest.json` |

`*.verify.png` are the extraction proof grids for eyeball review.

Publishing (S3 + Dynamo MerchantFont pointers) is owner-gated; see
`tools/glyph-studio/ADD_MERCHANT.md` step 9 and the W7 PR body for the exact
staged commands. Logos staged alongside in `../logo_masters/`.
