import { knockOutReceiptPaper, stampThermalDots } from "./pixelKernels";

test("knockOutReceiptPaper converts luminance to transparent ink alpha", () => {
  const pixels = new Uint8ClampedArray([
    255, 255, 255, 255, // white paper -> transparent
    0, 0, 0, 255, // black ink -> opaque
    127, 127, 127, 255, // gray antialiasing -> partial alpha
    230, 230, 230, 255, // near-paper scan shading -> transparent
    0, 0, 0, 0, // transparent source remains transparent
  ]);

  knockOutReceiptPaper(pixels);

  expect(Array.from(pixels)).toEqual([
    0, 0, 0, 0,
    0, 0, 0, 255,
    0, 0, 0, 124,
    0, 0, 0, 0,
    0, 0, 0, 0,
  ]);
});

test("stampThermalDots clears the buffer and stamps a soft disk", () => {
  const width = 8;
  const height = 8;
  const pixels = new Uint8ClampedArray(width * height * 4);
  pixels.fill(255);
  const points = new Float32Array([3.5, 3.5]);

  stampThermalDots(pixels, {
    width,
    height,
    points,
    count: 1,
    radius: 2,
    red: 34,
    green: 34,
    blue: 34,
  });

  let opaque = 0;
  let soft = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    const a = pixels[i + 3];
    if (a === 255) {
      opaque += 1;
      expect(pixels[i]).toBe(34);
    } else if (a > 0) {
      soft += 1;
    }
  }
  expect(opaque).toBeGreaterThan(0);
  expect(soft).toBeGreaterThan(0);
  expect(opaque + soft).toBeLessThan(width * height);
});
