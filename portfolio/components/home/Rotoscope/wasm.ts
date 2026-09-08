import {
  createFocusTierMap,
  normalizeRotoscopeOptions,
  validateRgba,
  type FocusGeometry,
  type RotoscopeOptions,
  type RotoscopeResult,
} from "./algorithm";

const ROTOSCOPE_WASM_ABI_VERSION = 1;
const ROTOSCOPE_WASM_URL = "/wasm/rotoscope_v1.wasm";

interface RotoscopeWasmExports extends WebAssembly.Exports {
  memory: WebAssembly.Memory;
  abiVersion(): number;
  arenaBase(): number;
  requiredBytes(width: number, height: number, markerBudget: number): number;
  ensureCapacity(bytes: number): number;
  inputRgbaPtr(width: number, height: number, markerBudget: number): number;
  focusTierPtr(width: number, height: number, markerBudget: number): number;
  outputRgbaPtr(width: number, height: number, markerBudget: number): number;
  run(
    width: number,
    height: number,
    blurRadius: number,
    markerBudget: number,
    faceQuota: number,
    bodyQuota: number,
    backgroundQuota: number,
    faceSpacing: number,
    bodySpacing: number,
    backgroundSpacing: number,
  ): number;
  status(): number;
  markerCount(): number;
  faceMarkerCount(): number;
  bodyMarkerCount(): number;
  backgroundMarkerCount(): number;
}

export interface WasmRotoscopeRun {
  result: RotoscopeResult;
  loadMs: number;
  focusMapMs: number;
  pipelineMs: number;
  path: "wasm-scalar";
}

let wasmPromise: Promise<RotoscopeWasmExports | null> | undefined;
let cachedFocus:
  | { key: string; width: number; height: number; tiers: Uint8Array }
  | undefined;

const isRotoscopeExports = (
  value: WebAssembly.Exports,
): value is RotoscopeWasmExports => {
  const candidate = value as Partial<RotoscopeWasmExports>;
  return (
    candidate.memory instanceof WebAssembly.Memory &&
    typeof candidate.abiVersion === "function" &&
    candidate.abiVersion() === ROTOSCOPE_WASM_ABI_VERSION &&
    typeof candidate.arenaBase === "function" &&
    typeof candidate.requiredBytes === "function" &&
    typeof candidate.ensureCapacity === "function" &&
    typeof candidate.inputRgbaPtr === "function" &&
    typeof candidate.focusTierPtr === "function" &&
    typeof candidate.outputRgbaPtr === "function" &&
    typeof candidate.run === "function" &&
    typeof candidate.status === "function" &&
    typeof candidate.markerCount === "function" &&
    typeof candidate.faceMarkerCount === "function" &&
    typeof candidate.bodyMarkerCount === "function" &&
    typeof candidate.backgroundMarkerCount === "function"
  );
};

const instantiate = async (): Promise<RotoscopeWasmExports | null> => {
  try {
    const response = await fetch(ROTOSCOPE_WASM_URL, { cache: "force-cache" });
    if (!response.ok) return null;
    let instance: WebAssembly.Instance;
    if (typeof WebAssembly.instantiateStreaming === "function") {
      try {
        ({ instance } = await WebAssembly.instantiateStreaming(
          Promise.resolve(response.clone()),
          {},
        ));
      } catch {
        const bytes = await response.arrayBuffer();
        ({ instance } = await WebAssembly.instantiate(bytes, {}));
      }
    } else {
      const bytes = await response.arrayBuffer();
      ({ instance } = await WebAssembly.instantiate(bytes, {}));
    }
    return isRotoscopeExports(instance.exports) ? instance.exports : null;
  } catch {
    return null;
  }
};

const loadWasm = (): Promise<RotoscopeWasmExports | null> => {
  wasmPromise ??= instantiate();
  return wasmPromise;
};

const focusKey = (
  width: number,
  height: number,
  focus: FocusGeometry,
): string =>
  JSON.stringify([
    width,
    height,
    focus.face.centerX,
    focus.face.centerY,
    focus.face.radiusX,
    focus.face.radiusY,
    ...focus.body.flat(),
  ]);

const focusMap = (
  width: number,
  height: number,
  focus: FocusGeometry,
): Uint8Array => {
  const key = focusKey(width, height, focus);
  if (
    cachedFocus?.key === key &&
    cachedFocus.width === width &&
    cachedFocus.height === height
  ) {
    return cachedFocus.tiers;
  }
  const tiers = createFocusTierMap(width, height, focus);
  cachedFocus = { key, width, height, tiers };
  return tiers;
};

/** Runs the exact scalar contract in a reusable Wasm arena, or returns null. */
export const runRotoscopeWasm = async (
  source: Uint8ClampedArray,
  width: number,
  height: number,
  options: Partial<RotoscopeOptions> = {},
): Promise<WasmRotoscopeRun | null> => {
  const count = validateRgba(source, width, height);
  const normalized = normalizeRotoscopeOptions(options, count);
  const loadStartedAt = performance.now();
  const wasm = await loadWasm();
  const loadMs = performance.now() - loadStartedAt;
  if (!wasm) return null;

  try {
    const focusStartedAt = performance.now();
    const tiers = focusMap(width, height, normalized.focus);
    const focusMapMs = performance.now() - focusStartedAt;
    const bytes = wasm.requiredBytes(width, height, normalized.markerBudget);
    if (bytes <= 0 || wasm.ensureCapacity(bytes) !== 1) return null;

    // memory.grow detaches old ArrayBuffer views, so every view is created
    // only after ensureCapacity has finished.
    const inputPtr = wasm.inputRgbaPtr(width, height, normalized.markerBudget);
    const tierPtr = wasm.focusTierPtr(width, height, normalized.markerBudget);
    new Uint8Array(wasm.memory.buffer, inputPtr, source.byteLength).set(source);
    new Uint8Array(wasm.memory.buffer, tierPtr, count).set(tiers);

    const pipelineStartedAt = performance.now();
    const status = wasm.run(
      width,
      height,
      normalized.blurRadius,
      normalized.markerBudget,
      normalized.quotas.face,
      normalized.quotas.body,
      normalized.quotas.background,
      normalized.spacing.face,
      normalized.spacing.body,
      normalized.spacing.background,
    );
    const pipelineMs = performance.now() - pipelineStartedAt;
    if (status !== 0 || wasm.status() !== 0) return null;

    const outputPtr = wasm.outputRgbaPtr(width, height, normalized.markerBudget);
    const pixels = new Uint8ClampedArray(source.byteLength);
    pixels.set(new Uint8Array(wasm.memory.buffer, outputPtr, source.byteLength));
    return {
      result: {
        pixels,
        markerCount: wasm.markerCount(),
        tierCounts: {
          face: wasm.faceMarkerCount(),
          body: wasm.bodyMarkerCount(),
          background: wasm.backgroundMarkerCount(),
        },
        mode: "single-image",
      },
      loadMs,
      focusMapMs,
      pipelineMs,
      path: "wasm-scalar",
    };
  } catch {
    return null;
  }
};

/** Test-only cache reset for deterministic loader/fallback coverage. */
export const __resetRotoscopeWasmForTests = (): void => {
  wasmPromise = undefined;
  cachedFocus = undefined;
};
