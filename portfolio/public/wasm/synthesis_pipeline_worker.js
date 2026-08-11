/**
 * SynthesisPipeline pixel worker.
 * Runs knockout + thermal stamp off the main thread. Prefers the committed
 * WASM module; falls back to JS kernels inside the worker.
 */
"use strict";

/** @type {WebAssembly.Exports | null} */
let wasm = null;
let wasmFailed = false;

function knockOutJs(pixels) {
  const paperLuminance = 220;
  const solidInkLuminance = 70;
  for (let i = 0; i < pixels.length; i += 4) {
    const luminance = Math.round(
      pixels[i] * 0.2126 + pixels[i + 1] * 0.7152 + pixels[i + 2] * 0.0722,
    );
    const normalizedInk = Math.min(
      1,
      Math.max(0, (paperLuminance - luminance) / (paperLuminance - solidInkLuminance)),
    );
    const inkAlpha = normalizedInk ** 1.5;
    pixels[i + 3] = Math.round(pixels[i + 3] * inkAlpha);
    pixels[i] = 0;
    pixels[i + 1] = 0;
    pixels[i + 2] = 0;
  }
}

function stampThermalJs(pixels, width, height, points, count, radius, red, green, blue) {
  pixels.fill(0);
  if (width <= 0 || height <= 0 || count <= 0 || radius <= 0) {
    return;
  }
  const outer = radius + 0.5;
  const outer2 = outer * outer;
  const inner = Math.max(0, radius - 0.5);
  const inner2 = inner * inner;
  const ir = Math.ceil(outer);
  for (let i = 0; i < count; i += 1) {
    const cx = points[i * 2];
    const cy = points[i * 2 + 1];
    const x0 = Math.max(0, Math.floor(cx - ir));
    const x1 = Math.min(width - 1, Math.ceil(cx + ir));
    const y0 = Math.max(0, Math.floor(cy - ir));
    const y1 = Math.min(height - 1, Math.ceil(cy + ir));
    for (let y = y0; y <= y1; y += 1) {
      const dy = y + 0.5 - cy;
      const dy2 = dy * dy;
      for (let x = x0; x <= x1; x += 1) {
        const dx = x + 0.5 - cx;
        const d2 = dx * dx + dy2;
        if (d2 > outer2) continue;
        let coverage = 1;
        if (d2 > inner2) {
          coverage = Math.max(0, Math.min(1, outer - Math.sqrt(d2)));
        }
        const alpha = Math.round(coverage * 255);
        if (!alpha) continue;
        const o = (y * width + x) * 4;
        if (alpha >= pixels[o + 3]) {
          pixels[o] = red;
          pixels[o + 1] = green;
          pixels[o + 2] = blue;
          pixels[o + 3] = alpha;
        }
      }
    }
  }
}

/** @type {Promise<WebAssembly.Exports | null> | null} */
let wasmInit = null;

async function ensureWasm() {
  if (wasm || wasmFailed) return wasm;
  if (!wasmInit) {
    wasmInit = (async () => {
      try {
        const response = await fetch("/wasm/synthesis_pipeline.wasm");
        if (!response.ok) {
          wasmFailed = true;
          return null;
        }
        let result;
        try {
          result = await WebAssembly.instantiateStreaming(response.clone());
        } catch {
          const buffer = await response.arrayBuffer();
          result = await WebAssembly.instantiate(buffer);
        }
        wasm = result.instance.exports;
        return wasm;
      } catch {
        wasmFailed = true;
        return null;
      }
    })();
  }
  return wasmInit;
}

function knockOutWithWasm(exports, pixels) {
  const byteLength = pixels.byteLength;
  const base = exports.bufferBase();
  if (!exports.ensureCapacity(byteLength)) {
    knockOutJs(pixels);
    return;
  }
  new Uint8Array(exports.memory.buffer, base, byteLength).set(pixels);
  exports.knockOutReceiptPaper(base, byteLength);
  pixels.set(new Uint8Array(exports.memory.buffer, base, byteLength));
}

function stampWithWasm(exports, pixels, width, height, points, count, radius, red, green, blue) {
  const safeCount = Math.min(count, points.length >> 1);
  const pixelBytes = width * height * 4;
  const base = exports.bufferBase();
  if (!exports.ensureCapacity(pixelBytes + safeCount * 8)) {
    stampThermalJs(pixels, width, height, points, safeCount, radius, red, green, blue);
    return;
  }
  const pointsPtr = base + pixelBytes;
  new Float32Array(exports.memory.buffer, pointsPtr, safeCount * 2).set(
    points.subarray(0, safeCount * 2),
  );
  exports.stampThermalDots(
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
  pixels.set(new Uint8Array(exports.memory.buffer, base, pixelBytes));
}

self.onmessage = async (event) => {
  const msg = event.data || {};
  const { id, type } = msg;
  try {
    if (type === "knockOut") {
      const pixels = new Uint8ClampedArray(msg.buffer);
      const exports = await ensureWasm();
      if (exports) {
        knockOutWithWasm(exports, pixels);
      } else {
        knockOutJs(pixels);
      }
      self.postMessage({ id, ok: true, buffer: pixels.buffer }, [pixels.buffer]);
      return;
    }
    if (type === "stampThermal") {
      const { width, height, count, radius, red, green, blue } = msg;
      const points = new Float32Array(msg.pointsBuffer);
      const pixels = new Uint8ClampedArray(width * height * 4);
      const exports = await ensureWasm();
      if (exports) {
        stampWithWasm(
          exports,
          pixels,
          width,
          height,
          points,
          count,
          radius,
          red,
          green,
          blue,
        );
      } else {
        stampThermalJs(
          pixels,
          width,
          height,
          points,
          count,
          radius,
          red,
          green,
          blue,
        );
      }
      self.postMessage({ id, ok: true, buffer: pixels.buffer }, [pixels.buffer]);
      return;
    }
    self.postMessage({ id, ok: false, error: "unknown type" });
  } catch (err) {
    self.postMessage({
      id,
      ok: false,
      error: err && err.message ? err.message : String(err),
    });
  }
};
