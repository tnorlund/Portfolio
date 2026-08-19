/* global WebAssembly, console, process */

import { readFile } from "node:fs/promises";

const [referenceUrl, wasmPath] = process.argv.slice(2);
if (!referenceUrl || !wasmPath) {
  throw new Error("usage: node verify.mjs <reference-module-url> <wasm-path>");
}

const reference = await import(referenceUrl);
const wasmBytes = await readFile(wasmPath);
const { instance } = await WebAssembly.instantiate(wasmBytes);
const wasm = instance.exports;

const requiredExports = [
  "memory",
  "abiVersion",
  "arenaBase",
  "requiredBytes",
  "ensureCapacity",
  "inputRgbaPtr",
  "focusTierPtr",
  "outputRgbaPtr",
  "run",
  "status",
  "markerCount",
  "faceMarkerCount",
  "bodyMarkerCount",
  "backgroundMarkerCount",
];
for (const name of requiredExports) {
  if (!(name in wasm)) throw new Error(`missing Wasm export: ${name}`);
}
if (wasm.abiVersion() !== 1) throw new Error("unexpected Wasm ABI version");

const defaults = reference.DEFAULT_ROTOSCOPE_OPTIONS;

const runWasm = (source, width, height, options = {}) => {
  const markerBudget = Math.min(
    width * height,
    Math.max(1, Math.round(options.markerBudget ?? defaults.markerBudget)),
  );
  const bytes = wasm.requiredBytes(width, height, markerBudget);
  if (bytes <= 0 || wasm.ensureCapacity(bytes) !== 1) {
    throw new Error("Wasm arena allocation failed");
  }
  // ensureCapacity may replace memory.buffer, so create views afterwards.
  const inputPtr = wasm.inputRgbaPtr(width, height, markerBudget);
  const tiersPtr = wasm.focusTierPtr(width, height, markerBudget);
  new Uint8Array(wasm.memory.buffer, inputPtr, source.length).set(source);
  new Uint8Array(wasm.memory.buffer, tiersPtr, width * height).set(
    reference.createFocusTierMap(
      width,
      height,
      options.focus ?? defaults.focus,
    ),
  );
  const quotas = options.quotas ?? defaults.quotas;
  const spacing = options.spacing ?? defaults.spacing;
  const status = wasm.run(
    width,
    height,
    Math.round(options.blurRadius ?? defaults.blurRadius),
    markerBudget,
    quotas.face,
    quotas.body,
    quotas.background,
    Math.round(spacing.face),
    Math.round(spacing.body),
    Math.round(spacing.background),
  );
  if (status !== 0 || wasm.status() !== 0) {
    throw new Error(`Wasm run failed with status ${status}`);
  }
  const outputPtr = wasm.outputRgbaPtr(width, height, markerBudget);
  return {
    pixels: new Uint8ClampedArray(
      new Uint8Array(wasm.memory.buffer, outputPtr, source.length).slice().buffer,
    ),
    markerCount: wasm.markerCount(),
    tierCounts: {
      face: wasm.faceMarkerCount(),
      body: wasm.bodyMarkerCount(),
      background: wasm.backgroundMarkerCount(),
    },
  };
};

let randomState = 0x7f4a7c15;
const randomByte = () => {
  randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0;
  return randomState >>> 24;
};

const rgba = (width, height, pixel) => {
  const output = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      output.set(pixel(x, y), (y * width + x) * 4);
    }
  }
  return output;
};

const fixtures = [
  {
    name: "one-pixel",
    width: 1,
    height: 1,
    source: rgba(1, 1, () => [17, 43, 211, 93]),
    options: { blurRadius: 64, markerBudget: 1 },
  },
  {
    name: "flat-ties",
    width: 3,
    height: 1,
    source: rgba(3, 1, (x) => [x * 100, x * 100, x * 100, 100 + x]),
    options: { blurRadius: 1, markerBudget: 1 },
  },
  {
    name: "tiny-blur-wider-than-image",
    width: 5,
    height: 3,
    source: rgba(5, 3, (x, y) => [x * 31, y * 71, (x + y) * 19, 255 - x]),
    options: { blurRadius: 12, markerBudget: 4 },
  },
  {
    name: "odd-alpha",
    width: 17,
    height: 13,
    source: rgba(17, 13, (x, y) => [
      (x * 17 + y * 3) % 256,
      (x * 5 + y * 19) % 256,
      (x * 11 + y * 7) % 256,
      (x + y) % 2 === 0 ? 255 : 180,
    ]),
    options: {
      blurRadius: 2,
      markerBudget: 12,
      spacing: { face: 1, body: 1, background: 1 },
    },
  },
  {
    name: "zero-and-negative-quotas",
    width: 19,
    height: 15,
    source: rgba(19, 15, () => [randomByte(), randomByte(), randomByte(), randomByte()]),
    options: {
      blurRadius: 3,
      markerBudget: 19,
      quotas: { face: 1, body: -10, background: 1 },
      spacing: { face: 2, body: 3, background: 4 },
    },
  },
  {
    name: "representative-random",
    width: 80,
    height: 60,
    source: rgba(80, 60, () => [randomByte(), randomByte(), randomByte(), randomByte()]),
    options: {
      blurRadius: 9,
      markerBudget: 72,
      quotas: { face: 0.55, body: 0.3, background: 0.15 },
      spacing: { face: 2, body: 4, background: 8 },
    },
  },
  {
    name: "production-size-random",
    width: 480,
    height: 360,
    source: rgba(480, 360, () => [
      randomByte(),
      randomByte(),
      randomByte(),
      randomByte(),
    ]),
    options: {
      blurRadius: 9,
      markerBudget: 720,
      quotas: { face: 0.55, body: 0.3, background: 0.15 },
      spacing: { face: 2, body: 4, background: 8 },
    },
  },
];

for (const fixture of fixtures) {
  const expected = reference.runRotoscope(
    fixture.source,
    fixture.width,
    fixture.height,
    fixture.options,
  );
  const actual = runWasm(
    fixture.source,
    fixture.width,
    fixture.height,
    fixture.options,
  );
  const pixelMismatch = actual.pixels.findIndex(
    (value, index) => value !== expected.pixels[index],
  );
  if (pixelMismatch >= 0) {
    throw new Error(
      `${fixture.name}: pixel ${pixelMismatch} expected ${expected.pixels[pixelMismatch]}, got ${actual.pixels[pixelMismatch]}\nexpected ${Array.from(expected.pixels)}\nactual   ${Array.from(actual.pixels)}`,
    );
  }
  if (actual.markerCount !== expected.markerCount) {
    throw new Error(
      `${fixture.name}: marker count expected ${expected.markerCount}, got ${actual.markerCount}`,
    );
  }
  for (const tier of ["face", "body", "background"]) {
    if (actual.tierCounts[tier] !== expected.tierCounts[tier]) {
      throw new Error(
        `${fixture.name}: ${tier} count expected ${expected.tierCounts[tier]}, got ${actual.tierCounts[tier]}`,
      );
    }
  }
  console.log(`ok ${fixture.name}`);
}
