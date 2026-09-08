# SynthesisPipeline WASM kernels

AssemblyScript pixel kernels used by the `/receipt` SynthesisPipeline figure:

- `knockOutReceiptPaper` — luminance → ink alpha for receipt canvases
- `stampThermalDots` — soft-disk thermal stamps into an RGBA buffer

## Build

From `portfolio/`:

```bash
npm run build:wasm
```

Writes `portfolio/public/wasm/synthesis_pipeline.wasm` (committed so static export / CI do not need `asc` at deploy time).

## Runtime

`components/ui/Figures/SynthesisPipeline/wasm/loader.ts` fetches `/wasm/synthesis_pipeline.wasm` and falls back to the JS references in `pixelKernels.ts` when WASM is unavailable.
