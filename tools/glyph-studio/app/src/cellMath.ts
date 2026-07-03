// Bitmap cell-layout math, kept in exact parity with glyphstudio.cellmath
// (the python that generates fixtures/cellmath_cases.json).
//
// IMPORTANT: python round() is banker's rounding (round-half-to-even).
// Math.round differs on .5 halves, so we implement pyRound() and use it
// everywhere a python round() would run.

export function pyRound(x: number): number {
  const fl = Math.floor(x);
  const diff = x - fl;
  if (diff < 0.5) return fl;
  if (diff > 0.5) return fl + 1;
  // exactly halfway → round to even
  return fl % 2 === 0 ? fl : fl + 1;
}

export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export interface AtlasEntry {
  w: number;
  h: number;
  off: number;
  rows?: string[];
  bits_b64?: string;
}
export type Atlas = Record<number, AtlasEntry>;

const CAP_SET = "ABDEFGHKLMNPRSTUVXZ";
const ADV_SET = "MWHNUABDOR";

function presentHeights(atlas: Atlas, chars: string): number[] {
  const out: number[] = [];
  for (const ch of chars) {
    const g = atlas[ch.codePointAt(0)!];
    if (g) out.push(g.h);
  }
  return out;
}
function presentWidths(atlas: Atlas, chars: string): number[] {
  const out: number[] = [];
  for (const ch of chars) {
    const g = atlas[ch.codePointAt(0)!];
    if (g) out.push(g.w);
  }
  return out;
}

// capH = median(heights of CAP_SET present)
export function capHeight(atlas: Atlas): number {
  return median(presentHeights(atlas, CAP_SET));
}

// advanceRatio = (median(widths of ADV_SET present) + 2.0) / capH
export function advanceRatio(atlas: Atlas, capH: number): number {
  return (median(presentWidths(atlas, ADV_SET)) + 2.0) / capH;
}

// cellW = capPx * advanceRatio * condense
export function cellWidth(
  capPx: number,
  advanceR: number,
  condense: number,
): number {
  return capPx * advanceR * condense;
}

export interface ScaledGlyph {
  scaled_h: number;
  scaled_w: number;
  final_w: number;
  off_px: number;
}

// Per-glyph scaling. Matches the fixture: heights are NOT force-clamped to
// capPx here — the fixture's `$` (h=66) scales to 18 at cap 16, not 16. The
// caps-forcing described for draw_token_chars is a separate render concern.
export function scaledGlyph(
  g: AtlasEntry,
  capPx: number,
  capH: number,
  cellW: number,
  condense: number,
): ScaledGlyph {
  const scale = capPx / capH;
  const h = Math.max(1, pyRound(g.h * scale));
  const w = Math.max(1, pyRound(g.w * scale));
  let final_w = w;
  if (condense < 0.999) final_w = Math.max(1, pyRound(w * condense));
  const maxW = Math.max(1, pyRound(cellW * 0.96));
  if (final_w > maxW) final_w = maxW;
  const off_px = pyRound(g.off * scale);
  return { scaled_h: h, scaled_w: w, final_w, off_px };
}

// Draw-time height for a glyph. draw_token_chars forces caps/digits/$ to the
// full cap pixel height (target_h = capPx); everything else uses the same
// unforced scaled height as scaledGlyph(). This is deliberately OUTSIDE the
// fixture-tested scaledGlyph path — the atlas glyph is unforced, forcing is
// a render concern only.
export function renderHeight(
  ch: string,
  g: AtlasEntry,
  capPx: number,
  capH: number,
): number {
  if (forcesCap(ch)) return capPx;
  return Math.max(1, pyRound(g.h * (capPx / capH)));
}

// --- bitmap helpers ---

// Unpack an atlas glyph's packed bits (np.packbits, MSB-first, row-major)
// into a w*h Uint8Array of 0/1.
export function unpackBits(bits_b64: string, w: number, h: number): Uint8Array {
  const bin = atob(bits_b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const out = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const byte = bytes[i >> 3];
    out[i] = (byte >> (7 - (i & 7))) & 1;
  }
  return out;
}

// Rows of "0101" strings → w*h Uint8Array.
export function rowsToBits(rows: string[]): Uint8Array {
  const h = rows.length;
  const w = rows[0]?.length ?? 0;
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const row = rows[y];
    for (let x = 0; x < w; x++) out[y * w + x] = row[x] === "1" ? 1 : 0;
  }
  return out;
}

// NEAREST-neighbour resize of a w0*h0 bitmap to w1*h1.
export function nearestResize(
  src: Uint8Array,
  w0: number,
  h0: number,
  w1: number,
  h1: number,
): Uint8Array {
  const out = new Uint8Array(w1 * h1);
  for (let y = 0; y < h1; y++) {
    const sy = Math.min(h0 - 1, Math.floor((y * h0) / h1));
    for (let x = 0; x < w1; x++) {
      const sx = Math.min(w0 - 1, Math.floor((x * w0) / w1));
      out[y * w1 + x] = src[sy * w0 + sx];
    }
  }
  return out;
}

const PRESERVE_TOP_CHARS = "oceCO0QG";

// thinInkMask: drop a deterministic fraction of edge ink pixels.
export function thinInkMask(
  bits: Uint8Array,
  w: number,
  h: number,
  amount: number,
  ch: string,
): Uint8Array {
  if (amount <= 0) return bits;
  const preserveTop = PRESERVE_TOP_CHARS.includes(ch);
  const mod = Math.max(2, pyRound(1 / amount));
  const out = new Uint8Array(bits);
  const solid = (x: number, y: number) =>
    x >= 0 && x < w && y >= 0 && y < h && bits[y * w + x] === 1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (bits[y * w + x] !== 1) continue;
      const isEdge =
        !solid(x - 1, y) || !solid(x + 1, y) || !solid(x, y - 1) || !solid(x, y + 1);
      if (!isEdge) continue;
      if ((x * 17 + y * 31 + w * 7 + h * 11) % mod !== 0) continue;
      if (preserveTop && y < Math.max(1, pyRound(h * 0.32))) continue;
      out[y * w + x] = 0;
    }
  }
  return out;
}

// Which chars are drawn as caps by draw_token_chars.
export function forcesCap(ch: string): boolean {
  return /[A-Z0-9$]/.test(ch);
}
