/**
 * Load the committed SynthesisPipeline WASM module from `/wasm/`.
 * Works with Next `output: "export"` (static files under `public/`).
 */

export interface SynthesisPipelineWasmExports {
  memory: WebAssembly.Memory;
  ensureCapacity(bytes: number): void;
  knockOutReceiptPaper(pixelPtr: number, byteLength: number): void;
  clearRgba(pixelPtr: number, byteLength: number): void;
  stampThermalDots(
    pixelPtr: number,
    width: number,
    height: number,
    pointsPtr: number,
    count: number,
    radius: number,
    red: number,
    green: number,
    blue: number,
  ): void;
}

const WASM_URL = "/wasm/synthesis_pipeline.wasm";

let loadPromise: Promise<SynthesisPipelineWasmExports | null> | null = null;

const asExports = (
  value: WebAssembly.Exports,
): SynthesisPipelineWasmExports | null => {
  const candidate = value as Partial<SynthesisPipelineWasmExports>;
  if (
    !(candidate.memory instanceof WebAssembly.Memory) ||
    typeof candidate.ensureCapacity !== "function" ||
    typeof candidate.knockOutReceiptPaper !== "function" ||
    typeof candidate.clearRgba !== "function" ||
    typeof candidate.stampThermalDots !== "function"
  ) {
    return null;
  }
  return candidate as SynthesisPipelineWasmExports;
};

const instantiateFromResponse = async (
  response: Response,
): Promise<WebAssembly.WebAssemblyInstantiatedSource> => {
  if (typeof WebAssembly.instantiateStreaming === "function") {
    try {
      return await WebAssembly.instantiateStreaming(response.clone());
    } catch {
      // Fall through when MIME type is wrong (some static hosts).
    }
  }
  const buffer = await response.arrayBuffer();
  return WebAssembly.instantiate(buffer);
};

/**
 * Lazily load WASM once. Resolves to `null` when unavailable (SSR, jsdom,
 * network failure) so callers can use the JS reference kernels.
 */
export const loadSynthesisPipelineWasm = (): Promise<SynthesisPipelineWasmExports | null> => {
  if (typeof WebAssembly === "undefined") {
    return Promise.resolve(null);
  }
  if (!loadPromise) {
    loadPromise = (async () => {
      try {
        const response = await fetch(WASM_URL);
        if (!response.ok) {
          return null;
        }
        const result = await instantiateFromResponse(response);
        return asExports(result.instance.exports);
      } catch {
        return null;
      }
    })();
  }
  return loadPromise;
};

/** Test-only: reset the memoized loader promise. */
export const __resetSynthesisPipelineWasmForTests = (): void => {
  loadPromise = null;
};
