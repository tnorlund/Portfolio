import {
  knockOutReceiptPaper as knockOutJs,
  stampThermalDots as stampThermalJs,
  ThermalStampParams,
} from "../pixelKernels";
import {
  loadSynthesisPipelineWasm,
  SynthesisPipelineWasmExports,
} from "./loader";

export type KernelPath = "wasm" | "js";

let cached: SynthesisPipelineWasmExports | null | undefined;

const getWasm = async (): Promise<SynthesisPipelineWasmExports | null> => {
  if (cached !== undefined) {
    return cached;
  }
  cached = await loadSynthesisPipelineWasm();
  return cached;
};

/** Test-only: clear cached exports so the next call reloads. */
export const __resetWasmKernelCacheForTests = (): void => {
  cached = undefined;
};

const blitPixels = (
  ctx: CanvasRenderingContext2D,
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  isCancelled?: () => boolean,
): boolean => {
  if (isCancelled?.()) {
    return false;
  }
  const copy = new Uint8ClampedArray(pixels);
  ctx.putImageData(new ImageData(copy, width, height), 0, 0);
  return true;
};

/**
 * Prefer WASM knock-out; fall back to the JS reference on any failure.
 * Mutates `pixels` in place in either path.
 */
export const knockOutReceiptPaperFast = async (
  pixels: Uint8ClampedArray,
): Promise<KernelPath> => {
  const wasm = await getWasm();
  if (!wasm) {
    knockOutJs(pixels);
    return "js";
  }
  try {
    const byteLength = pixels.byteLength;
    const base = wasm.bufferBase();
    if (!wasm.ensureCapacity(byteLength)) {
      knockOutJs(pixels);
      return "js";
    }
    new Uint8Array(wasm.memory.buffer, base, byteLength).set(pixels);
    wasm.knockOutReceiptPaper(base, byteLength);
    pixels.set(new Uint8Array(wasm.memory.buffer, base, byteLength));
    return "wasm";
  } catch {
    knockOutJs(pixels);
    return "js";
  }
};

/**
 * Knock out receipt paper on a canvas ImageData and blit back.
 * Skips putImageData when `isCancelled()` is true.
 */
export const knockOutAndBlit = async (
  ctx: CanvasRenderingContext2D,
  imageData: ImageData,
  isCancelled?: () => boolean,
): Promise<KernelPath> => {
  const path = await knockOutReceiptPaperFast(imageData.data);
  blitPixels(
    ctx,
    imageData.data,
    imageData.width,
    imageData.height,
    isCancelled,
  );
  return path;
};

/**
 * Prefer WASM thermal stamping; blit from WASM memory when possible.
 */
export const stampThermalDotsAndBlit = async (
  ctx: CanvasRenderingContext2D,
  params: ThermalStampParams,
  isCancelled?: () => boolean,
): Promise<KernelPath> => {
  const { width, height, points, count, radius, red, green, blue } = params;
  const wasm = await getWasm();
  if (!wasm) {
    const pixels = new Uint8ClampedArray(width * height * 4);
    stampThermalJs(pixels, params);
    blitPixels(ctx, pixels, width, height, isCancelled);
    return "js";
  }
  try {
    const safeCount = Math.min(count, points.length >> 1);
    const pixelBytes = width * height * 4;
    const pointsBytes = safeCount * 8;
    const base = wasm.bufferBase();
    if (!wasm.ensureCapacity(pixelBytes + pointsBytes)) {
      const pixels = new Uint8ClampedArray(width * height * 4);
      stampThermalJs(pixels, { ...params, count: safeCount });
      blitPixels(ctx, pixels, width, height, isCancelled);
      return "js";
    }
    const pointsPtr = base + pixelBytes;
    new Float32Array(wasm.memory.buffer, pointsPtr, safeCount * 2).set(
      points.subarray(0, safeCount * 2),
    );
    wasm.stampThermalDots(
      base,
      width,
      height,
      pointsPtr,
      safeCount,
      radius,
      red,
      green,
      blue,
    );
    const out = new Uint8ClampedArray(wasm.memory.buffer, base, pixelBytes);
    blitPixels(ctx, out, width, height, isCancelled);
    return "wasm";
  } catch {
    const pixels = new Uint8ClampedArray(width * height * 4);
    stampThermalJs(pixels, params);
    blitPixels(ctx, pixels, width, height, isCancelled);
    return "js";
  }
};

/**
 * Prefer WASM thermal stamping into `pixels`; fall back to JS.
 * Kept for unit tests that assert buffer equality.
 */
export const stampThermalDotsFast = async (
  pixels: Uint8ClampedArray,
  params: ThermalStampParams,
): Promise<KernelPath> => {
  const wasm = await getWasm();
  if (!wasm) {
    stampThermalJs(pixels, params);
    return "js";
  }
  try {
    const { width, height, points, count, radius, red, green, blue } = params;
    const safeCount = Math.min(count, points.length >> 1);
    const pixelBytes = width * height * 4;
    const pointsBytes = safeCount * 8;
    const base = wasm.bufferBase();
    if (!wasm.ensureCapacity(pixelBytes + pointsBytes)) {
      stampThermalJs(pixels, { ...params, count: safeCount });
      return "js";
    }
    const pointsPtr = base + pixelBytes;
    new Float32Array(wasm.memory.buffer, pointsPtr, safeCount * 2).set(
      points.subarray(0, safeCount * 2),
    );
    wasm.stampThermalDots(
      base,
      width,
      height,
      pointsPtr,
      safeCount,
      radius,
      red,
      green,
      blue,
    );
    pixels.set(new Uint8Array(wasm.memory.buffer, base, pixelBytes));
    return "wasm";
  } catch {
    stampThermalJs(pixels, params);
    return "js";
  }
};

/** Synchronous JS-only path for Jest and reduced-motion first paint. */
export { knockOutJs as knockOutReceiptPaperJs, stampThermalJs as stampThermalDotsJs };
