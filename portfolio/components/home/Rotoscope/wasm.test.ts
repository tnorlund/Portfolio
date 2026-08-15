import fs from "fs";
import path from "path";
import sharp from "sharp";
import { runRotoscope, type RotoscopeOptions } from "./algorithm";
import {
  __resetRotoscopeWasmForTests,
  runRotoscopeWasm,
} from "./wasm";
import { PORTRAIT_ROTOSCOPE_OPTIONS } from "./portraitConfig";

const WASM_PATH = path.join(
  __dirname,
  "../../../public/wasm/rotoscope_v1.wasm",
);
const PORTRAIT_PATH = path.join(
  __dirname,
  "../../../public/rotoscope-portrait.jpg",
);

const rgba = (
  width: number,
  height: number,
  pixel: (x: number, y: number) => readonly [number, number, number, number],
): Uint8ClampedArray => {
  const output = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      output.set(pixel(x, y), (y * width + x) * 4);
    }
  }
  return output;
};

const mockWasmFetch = (ok = true): jest.Mock => {
  const wasmBytes = fs.readFileSync(WASM_PATH);
  const fetchMock = jest.fn(async () => {
    if (!ok) return { ok: false } as Response;
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
  });
  global.fetch = fetchMock as typeof fetch;
  // The byte-buffer path is deterministic in jsdom and still exercises the
  // same compiled artifact used by the worker.
  // @ts-expect-error intentional test override
  WebAssembly.instantiateStreaming = undefined;
  return fetchMock;
};

const expectExactParity = async (
  source: Uint8ClampedArray,
  width: number,
  height: number,
  options: Partial<RotoscopeOptions>,
): Promise<void> => {
  const expected = runRotoscope(source, width, height, options);
  const accelerated = await runRotoscopeWasm(source, width, height, options);
  expect(accelerated?.path).toBe("wasm-scalar");
  expect(accelerated?.result.markerCount).toBe(expected.markerCount);
  expect(accelerated?.result.tierCounts).toEqual(expected.tierCounts);
  expect(
    Buffer.compare(
      Buffer.from(accelerated?.result.pixels ?? []),
      Buffer.from(expected.pixels),
    ),
  ).toBe(0);
};

beforeEach(() => {
  __resetRotoscopeWasmForTests();
});

afterEach(() => {
  jest.restoreAllMocks();
});

test("Wasm exactly matches flat, tiny, odd, alpha, and wide-blur fixtures", async () => {
  mockWasmFetch();
  await expectExactParity(
    rgba(3, 1, (x) => [x * 100, x * 100, x * 100, 100 + x]),
    3,
    1,
    { blurRadius: 64, markerBudget: 1 },
  );
  await expectExactParity(
    rgba(17, 13, (x, y) => [
      (x * 17 + y * 3) % 256,
      (x * 5 + y * 19) % 256,
      (x * 11 + y * 7) % 256,
      (x + y) % 2 === 0 ? 255 : 37,
    ]),
    17,
    13,
    {
      blurRadius: 2,
      markerBudget: 12,
      spacing: { face: 1, body: 1, background: 1 },
    },
  );
});

test("Wasm matches adversarial quota normalization and budget one", async () => {
  mockWasmFetch();
  const source = rgba(11, 9, (x, y) => [x * 17, y * 23, (x + y) * 11, 255]);
  await expectExactParity(source, 11, 9, {
    markerBudget: 1,
    quotas: { face: 0.5, body: 0.5, background: 0 },
    spacing: { face: 1, body: 1, background: 1 },
  });
  await expectExactParity(source, 11, 9, {
    markerBudget: 7,
    quotas: { face: -1, body: 0, background: Number.NaN },
  });
});

test("Wasm matches the actual 480x360 homepage portrait byte for byte", async () => {
  mockWasmFetch();
  const { data, info } = await sharp(PORTRAIT_PATH)
    .resize(480, 360, { fit: "fill" })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  expect(info.width).toBe(480);
  expect(info.height).toBe(360);
  const source = new Uint8ClampedArray(
    data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
  );
  await expectExactParity(source, 480, 360, PORTRAIT_ROTOSCOPE_OPTIONS);
});

test("Wasm load is cached and an unavailable artifact falls back cleanly", async () => {
  const fetchMock = mockWasmFetch();
  const source = rgba(1, 1, () => [10, 20, 30, 40]);
  expect(await runRotoscopeWasm(source, 1, 1, {})).not.toBeNull();
  expect(await runRotoscopeWasm(source, 1, 1, {})).not.toBeNull();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  __resetRotoscopeWasmForTests();
  mockWasmFetch(false);
  expect(await runRotoscopeWasm(source, 1, 1, {})).toBeNull();
});
