import fs from "fs";
import path from "path";
import { knockOutReceiptPaper, stampThermalDots } from "../pixelKernels";
import {
  __resetWasmKernelCacheForTests,
  knockOutReceiptPaperFast,
  stampThermalDotsFast,
} from "./kernels";
import { __resetSynthesisPipelineWasmForTests } from "./loader";

const WASM_PATH = path.join(
  __dirname,
  "../../../../../public/wasm/synthesis_pipeline.wasm",
);

beforeEach(() => {
  __resetSynthesisPipelineWasmForTests();
  __resetWasmKernelCacheForTests();
});

afterEach(() => {
  jest.restoreAllMocks();
});

const mockWasmFetch = (ok: boolean): void => {
  const wasmBytes = fs.readFileSync(WASM_PATH);
  global.fetch = jest.fn(async () => {
    if (!ok) {
      return { ok: false } as Response;
    }
    return {
      ok: true,
      clone() {
        return this;
      },
      arrayBuffer: async () =>
        wasmBytes.buffer.slice(
          wasmBytes.byteOffset,
          wasmBytes.byteOffset + wasmBytes.byteLength,
        ),
    } as Response;
  }) as typeof fetch;
  // Force the arrayBuffer path (jsdom streaming is unreliable).
  // @ts-expect-error intentional for test
  WebAssembly.instantiateStreaming = undefined;
};

test("WASM knockOut matches the JS reference on the fixture buffer", async () => {
  mockWasmFetch(true);

  const jsPixels = new Uint8ClampedArray([
    255, 255, 255, 255,
    0, 0, 0, 255,
    127, 127, 127, 255,
    230, 230, 230, 255,
    0, 0, 0, 0,
  ]);
  const wasmPixels = new Uint8ClampedArray(jsPixels);
  knockOutReceiptPaper(jsPixels);
  const pathUsed = await knockOutReceiptPaperFast(wasmPixels);

  expect(pathUsed).toBe("wasm");
  expect(Array.from(wasmPixels)).toEqual(Array.from(jsPixels));
});

test("WASM thermal stamp matches the JS reference", async () => {
  mockWasmFetch(true);

  const width = 16;
  const height = 16;
  const points = new Float32Array([4, 4, 10.5, 11.25]);
  const params = {
    width,
    height,
    points,
    count: 2,
    radius: 2.5,
    red: 20,
    green: 30,
    blue: 40,
  };
  const jsPixels = new Uint8ClampedArray(width * height * 4);
  const wasmPixels = new Uint8ClampedArray(width * height * 4);
  stampThermalDots(jsPixels, params);
  const pathUsed = await stampThermalDotsFast(wasmPixels, params);

  expect(pathUsed).toBe("wasm");
  expect(Array.from(wasmPixels)).toEqual(Array.from(jsPixels));
});

test("fast kernels fall back to JS when WASM fetch fails", async () => {
  mockWasmFetch(false);

  const pixels = new Uint8ClampedArray([0, 0, 0, 255, 255, 255, 255, 255]);
  const pathUsed = await knockOutReceiptPaperFast(pixels);

  expect(pathUsed).toBe("js");
  expect(Array.from(pixels)).toEqual([0, 0, 0, 255, 0, 0, 0, 0]);
});

test("WASM stamp with count=0 clears shared memory instead of leaving leftovers", async () => {
  mockWasmFetch(true);

  const width = 8;
  const height = 8;
  // First paint some dots so memory is dirty.
  const dirty = new Uint8ClampedArray(width * height * 4);
  await stampThermalDotsFast(dirty, {
    width,
    height,
    points: new Float32Array([3.5, 3.5]),
    count: 1,
    radius: 2,
    red: 10,
    green: 20,
    blue: 30,
  });
  expect(dirty.some((v, i) => i % 4 === 3 && v > 0)).toBe(true);

  const cleared = new Uint8ClampedArray(width * height * 4);
  cleared.fill(255);
  const pathUsed = await stampThermalDotsFast(cleared, {
    width,
    height,
    points: new Float32Array(0),
    count: 0,
    radius: 2,
    red: 10,
    green: 20,
    blue: 30,
  });

  expect(pathUsed).toBe("wasm");
  expect(Array.from(cleared).every((v) => v === 0)).toBe(true);
});
