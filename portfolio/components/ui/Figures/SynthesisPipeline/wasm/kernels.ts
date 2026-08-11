import {
  knockOutReceiptPaper as knockOutJs,
  stampThermalDots as stampThermalJs,
  ThermalStampParams,
} from "../pixelKernels";
import {
  loadSynthesisPipelineWasm,
  SynthesisPipelineWasmExports,
} from "./loader";

/** Pixel region starts at 0; points follow after the RGBA buffer. */
const PIXEL_PTR = 0;

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

/**
 * Prefer WASM knock-out; fall back to the JS reference on any failure.
 * Mutates `pixels` in place in either path.
 */
export const knockOutReceiptPaperFast = async (
  pixels: Uint8ClampedArray,
): Promise<"wasm" | "js"> => {
  const wasm = await getWasm();
  if (!wasm) {
    knockOutJs(pixels);
    return "js";
  }
  try {
    const byteLength = pixels.byteLength;
    wasm.ensureCapacity(byteLength);
    const view = new Uint8Array(wasm.memory.buffer, PIXEL_PTR, byteLength);
    view.set(pixels);
    wasm.knockOutReceiptPaper(PIXEL_PTR, byteLength);
    // memory.grow can detach the previous buffer — re-wrap after the call.
    pixels.set(new Uint8Array(wasm.memory.buffer, PIXEL_PTR, byteLength));
    return "wasm";
  } catch {
    knockOutJs(pixels);
    return "js";
  }
};

/**
 * Knock out receipt paper on a canvas ImageData and blit back.
 * Uses a single copy into WASM memory, then `putImageData` from a view of
 * that memory (no second full-buffer copy into the original ImageData).
 */
export const knockOutAndBlit = async (
  ctx: CanvasRenderingContext2D,
  imageData: ImageData,
): Promise<"wasm" | "js"> => {
  const wasm = await getWasm();
  if (!wasm) {
    knockOutJs(imageData.data);
    ctx.putImageData(imageData, 0, 0);
    return "js";
  }
  try {
    const { width, height, data } = imageData;
    const byteLength = data.byteLength;
    wasm.ensureCapacity(byteLength);
    new Uint8Array(wasm.memory.buffer, PIXEL_PTR, byteLength).set(data);
    wasm.knockOutReceiptPaper(PIXEL_PTR, byteLength);
    const out = new Uint8ClampedArray(wasm.memory.buffer, PIXEL_PTR, byteLength);
    ctx.putImageData(new ImageData(out, width, height), 0, 0);
    return "wasm";
  } catch {
    knockOutJs(imageData.data);
    ctx.putImageData(imageData, 0, 0);
    return "js";
  }
};

/**
 * Prefer WASM thermal stamping; blit from WASM memory when possible.
 */
export const stampThermalDotsAndBlit = async (
  ctx: CanvasRenderingContext2D,
  params: ThermalStampParams,
): Promise<"wasm" | "js"> => {
  const wasm = await getWasm();
  const { width, height, points, count, radius, red, green, blue } = params;
  if (!wasm) {
    const pixels = new Uint8ClampedArray(width * height * 4);
    stampThermalJs(pixels, params);
    ctx.putImageData(new ImageData(pixels, width, height), 0, 0);
    return "js";
  }
  try {
    const pixelBytes = width * height * 4;
    const pointsBytes = count * 8;
    wasm.ensureCapacity(pixelBytes + pointsBytes);
    const pointsPtr = pixelBytes;
    new Float32Array(wasm.memory.buffer, pointsPtr, count * 2).set(
      points.subarray(0, count * 2),
    );
    wasm.stampThermalDots(
      PIXEL_PTR,
      width,
      height,
      pointsPtr,
      count,
      radius,
      red,
      green,
      blue,
    );
    const out = new Uint8ClampedArray(wasm.memory.buffer, PIXEL_PTR, pixelBytes);
    ctx.putImageData(new ImageData(out, width, height), 0, 0);
    return "wasm";
  } catch {
    const pixels = new Uint8ClampedArray(width * height * 4);
    stampThermalJs(pixels, params);
    ctx.putImageData(new ImageData(pixels, width, height), 0, 0);
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
): Promise<"wasm" | "js"> => {
  const wasm = await getWasm();
  if (!wasm) {
    stampThermalJs(pixels, params);
    return "js";
  }
  try {
    const { width, height, points, count, radius, red, green, blue } = params;
    const pixelBytes = width * height * 4;
    const pointsBytes = count * 8;
    wasm.ensureCapacity(pixelBytes + pointsBytes);
    const pointsPtr = pixelBytes;
    new Float32Array(wasm.memory.buffer, pointsPtr, count * 2).set(
      points.subarray(0, count * 2),
    );
    wasm.stampThermalDots(
      PIXEL_PTR,
      width,
      height,
      pointsPtr,
      count,
      radius,
      red,
      green,
      blue,
    );
    pixels.set(new Uint8Array(wasm.memory.buffer, PIXEL_PTR, pixelBytes));
    return "wasm";
  } catch {
    stampThermalJs(pixels, params);
    return "js";
  }
};

/** Synchronous JS-only path for Jest and reduced-motion first paint. */
export { knockOutJs as knockOutReceiptPaperJs, stampThermalJs as stampThermalDotsJs };
