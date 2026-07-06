# receipt-logo

`receipt-logo` converts merchant logo images into reusable SVG path assets for
receipt rendering and exposes the workflow as local CLI/MCP tools.

The package is isolated from the existing receipt data and labeling code. It can
read Portfolio-style receipt JSON fixtures for placement checks, but those reads
are explicitly read-only.

## Commands

From the Portfolio repo root:

```bash
PYTHONPATH=receipt_logo python -m receipt_logo.cli vectorize \
  ~/Downloads/Sprouts_Farmers_Market_Logo.png \
  --slug sprouts_farmers_market \
  --max-colors 2 \
  --output-dir /tmp/sprouts-logo-proof
```

Run the Sprouts proof workflow:

```bash
PYTHONPATH=receipt_logo python -m receipt_logo.cli prove-sprouts \
  --source ~/Downloads/Sprouts_Farmers_Market_Logo.png \
  --output-dir /tmp/sprouts-logo-proof
```

Run the MCP server:

```bash
PYTHONPATH=receipt_logo python scripts/receipt_logo_mcp_server.py
```

## Included Sprouts Bootstrap Asset

`receipt_logo/receipt_logo/assets/merchant_logos/sprouts_farmers_market.svg` is
the first checked-in path asset generated from the downloaded Sprouts logo
source. The bootstrap trace produced:

- 2 color layers: `#2a783a`, `#6bbd45`
- 34 path contours
- 893 path points
- source dimensions: `1800 x 468`

The receipt-scale proof asset is checked in at
`receipt_logo/receipt_logo/assets/merchant_logos/sprouts_receipt_header.svg`.


## Integration note (2026-07-06)

The supported surfaces are the **python package + CLI**. The standalone
python MCP server (`mcp_server.py`) requires the `mcp` package, which is
deliberately NOT in the repo venv (dependency pins) — treat it as optional.
The sanctioned MCP surface is the existing **glyph-studio server**
(`tools/glyph-studio/server/mcp.mjs`), which shells this package's CLI and
needs no new python dependencies; `list_logos` / `vectorize_logo` land there
once this package merges.

Assets: `sprouts_farmers_market` (raster-traced) and `costco_wholesale`
(authored vector source, `source_kind: "vector"` — no vectorization pass).
