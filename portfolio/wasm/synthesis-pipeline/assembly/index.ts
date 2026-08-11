/**
 * SynthesisPipeline pixel kernels compiled to WebAssembly.
 *
 * Memory layout (managed by the JS host):
 * - A single growable ArrayBuffer owned by this module.
 * - Host allocates two regions via `ensureCapacity` / pointer helpers:
 *   1. RGBA pixel buffer (Uint8) at `pixelPtr`
 *   2. Optional XY point buffer (f32 pairs) at `pointsPtr`
 *
 * Exports operate in-place on those regions. No GC objects cross the boundary.
 */

/** Grow WASM memory so at least `bytes` are addressable from 0. */
export function ensureCapacity(bytes: i32): void {
  const pagesNeeded = (bytes + 0xffff) >> 16;
  const current = memory.size();
  if (pagesNeeded > current) {
    memory.grow(pagesNeeded - current);
  }
}

/**
 * Turn opaque dark-ink-on-white receipt pixels into black ink with real alpha.
 * Must match the JS `knockOutReceiptPaper` reference implementation bit-for-bit
 * for the fixtures used in unit tests.
 */
export function knockOutReceiptPaper(pixelPtr: i32, byteLength: i32): void {
  const paperLuminance: f32 = 220.0;
  const solidInkLuminance: f32 = 70.0;
  const denom: f32 = paperLuminance - solidInkLuminance;

  for (let i: i32 = 0; i < byteLength; i += 4) {
    const r = f32(load<u8>(pixelPtr + i));
    const g = f32(load<u8>(pixelPtr + i + 1));
    const b = f32(load<u8>(pixelPtr + i + 2));
    const a = load<u8>(pixelPtr + i + 3);

    // Math.round equivalent for non-negative values.
    const luminance = f32(i32(r * 0.2126 + g * 0.7152 + b * 0.0722 + 0.5));
    let normalizedInk = (paperLuminance - luminance) / denom;
    if (normalizedInk < 0.0) {
      normalizedInk = 0.0;
    } else if (normalizedInk > 1.0) {
      normalizedInk = 1.0;
    }
    const inkAlpha = Mathf.pow(normalizedInk, 1.5);
    const outA = i32(f32(a) * inkAlpha + 0.5);

    store<u8>(pixelPtr + i, 0);
    store<u8>(pixelPtr + i + 1, 0);
    store<u8>(pixelPtr + i + 2, 0);
    store<u8>(pixelPtr + i + 3, u8(outA));
  }
}

/**
 * Clear an RGBA buffer to transparent black.
 */
export function clearRgba(pixelPtr: i32, byteLength: i32): void {
  memory.fill(pixelPtr, 0, byteLength);
}

/**
 * Stamp filled thermal dots into an RGBA buffer.
 *
 * `pointsPtr` holds `count` interleaved f32 (x, y) pairs in the same pixel
 * space as the buffer. Circles use a hard disk with a 1px soft edge so
 * antialiasing roughly matches canvas `arc` fills at typical radii.
 */
export function stampThermalDots(
  pixelPtr: i32,
  width: i32,
  height: i32,
  pointsPtr: i32,
  count: i32,
  radius: f32,
  red: u8,
  green: u8,
  blue: u8,
): void {
  // Invalid canvas size: nothing to clear or stamp.
  if (width <= 0 || height <= 0) {
    return;
  }

  const byteLength = width * height * 4;
  // Zero dots / non-positive radius must still clear shared memory so a later
  // blit cannot show leftover knockout pixels or prior thermal frames.
  if (count <= 0 || radius <= 0.0) {
    clearRgba(pixelPtr, byteLength);
    return;
  }

  clearRgba(pixelPtr, byteLength);

  const outer = radius + 0.5;
  const outer2 = outer * outer;
  const inner = Mathf.max(0.0, radius - 0.5);
  const inner2 = inner * inner;
  const ir = i32(Mathf.ceil(outer));

  for (let i: i32 = 0; i < count; i += 1) {
    const cx = load<f32>(pointsPtr + i * 8);
    const cy = load<f32>(pointsPtr + i * 8 + 4);
    const x0 = max(0, i32(Mathf.floor(cx - f32(ir))));
    const x1 = min(width - 1, i32(Mathf.ceil(cx + f32(ir))));
    const y0 = max(0, i32(Mathf.floor(cy - f32(ir))));
    const y1 = min(height - 1, i32(Mathf.ceil(cy + f32(ir))));

    for (let y = y0; y <= y1; y += 1) {
      const dy = f32(y) + 0.5 - cy;
      const dy2 = dy * dy;
      for (let x = x0; x <= x1; x += 1) {
        const dx = f32(x) + 0.5 - cx;
        const d2 = dx * dx + dy2;
        if (d2 > outer2) {
          continue;
        }
        let coverage: f32 = 1.0;
        if (d2 > inner2) {
          // Linear falloff across the soft edge for cheap AA.
          const d = Mathf.sqrt(d2);
          coverage = Mathf.max(0.0, Mathf.min(1.0, outer - d));
        }
        const alpha = u8(i32(coverage * 255.0 + 0.5));
        if (alpha == 0) {
          continue;
        }
        const o = pixelPtr + (y * width + x) * 4;
        // Source-over onto transparent / existing ink (same color).
        const prevA = load<u8>(o + 3);
        if (alpha >= prevA) {
          store<u8>(o, red);
          store<u8>(o + 1, green);
          store<u8>(o + 2, blue);
          store<u8>(o + 3, alpha);
        }
      }
    }
  }
}
