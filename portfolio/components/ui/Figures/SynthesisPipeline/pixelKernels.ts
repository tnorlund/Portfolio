/**
 * JS reference implementations for SynthesisPipeline pixel kernels.
 * Kept in sync with the AssemblyScript module under
 * `portfolio/wasm/synthesis-pipeline/`. Tests and WASM-fallback paths use these.
 */

/**
 * Turn opaque dark-ink-on-white receipt pixels into black ink with real alpha.
 * The grayscale intensity is preserved as opacity, so antialiasing and the
 * consensus cloud survive while the receipt-paper pixels disappear entirely.
 */
export const knockOutReceiptPaper = (pixels: Uint8ClampedArray): void => {
  const paperLuminance = 220;
  const solidInkLuminance = 70;
  for (let i = 0; i < pixels.length; i += 4) {
    const luminance = Math.round(
      pixels[i] * 0.2126 + pixels[i + 1] * 0.7152 + pixels[i + 2] * 0.0722,
    );
    const normalizedInk = Math.min(
      1,
      Math.max(
        0,
        (paperLuminance - luminance) /
          (paperLuminance - solidInkLuminance),
      ),
    );
    const inkAlpha = normalizedInk ** 1.5;
    pixels[i + 3] = Math.round(pixels[i + 3] * inkAlpha);
    pixels[i] = 0;
    pixels[i + 1] = 0;
    pixels[i + 2] = 0;
  }
};

export interface ThermalStampParams {
  width: number;
  height: number;
  /** Interleaved x,y pairs in pixel space. */
  points: Float32Array;
  count: number;
  radius: number;
  red: number;
  green: number;
  blue: number;
}

/**
 * Stamp filled thermal dots into an RGBA buffer (clears first).
 * Soft 1px edge approximates canvas arc antialiasing.
 */
export const stampThermalDots = (
  pixels: Uint8ClampedArray,
  params: ThermalStampParams,
): void => {
  const { width, height, points, count, radius, red, green, blue } = params;
  if (width <= 0 || height <= 0 || count <= 0 || radius <= 0) {
    pixels.fill(0);
    return;
  }
  pixels.fill(0);
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
        if (d2 > outer2) {
          continue;
        }
        let coverage = 1;
        if (d2 > inner2) {
          const d = Math.sqrt(d2);
          coverage = Math.max(0, Math.min(1, outer - d));
        }
        const alpha = Math.round(coverage * 255);
        if (alpha === 0) {
          continue;
        }
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
};
