// Source schema for parametric stroke-skeleton fonts.
// COORDINATES: cap units, y-UP, baseline y=0, cap ink line y=1000.
// Stroke coords are CENTERLINES (ink extends dot/2 past them).

export interface DotParams {
  shape: "round" | "square";
  size: number;
  pitch: number;
}

export interface CornerParams {
  mode: string;
  tension: number;
}

export interface FontParams {
  dot: DotParams;
  weight: number;
  trackingScale: number;
  slant: number;
  corner: CornerParams;
  supersample: number;
}

export interface FontMetrics {
  capHeight: number;
  xHeight: number;
  ascender: number;
  descender: number;
}

export interface FontPreview {
  condense: number;
  capPx: number;
  thin: string | number;
}

export interface FontSource {
  version: 1;
  name: string;
  refCap: number;
  metrics: FontMetrics;
  params: FontParams;
  preview: FontPreview;
  review: Record<string, unknown>;
}

export interface Handle {
  x: number;
  y: number;
}

export interface Node {
  x: number;
  y: number;
  type: "corner" | "smooth";
  hIn?: Handle;
  hOut?: Handle;
}

export interface Stroke {
  closed: boolean;
  nodes: Node[];
}

export type Provenance = "traced" | "edited";

export interface GlyphTrace {
  corpus?: string;
  samples?: number;
  consensusHash?: string;
  date?: string;
  [k: string]: unknown;
}

export interface GlyphSource {
  version: 1;
  char: string;
  codepoint: number;
  provenance: Provenance;
  trace?: GlyphTrace;
  width: number;
  baselineNudgePx: number;
  overrides: Record<string, unknown>;
  strokes: Stroke[];
}

export interface FontBundle {
  font: FontSource;
  glyphs: Record<number, GlyphSource>;
  traced: number[];
}

// POST /api/raster response
export interface RasterResult {
  ok: boolean;
  png_b64: string;
  w: number;
  h: number;
  off: number;
}

// Atlas glyph from compile / cellmath fixtures
export interface AtlasGlyph {
  w: number;
  h: number;
  off: number;
  bits_b64?: string;
  rows?: string[];
}

export interface CompileResult {
  log: string;
  sheet: string;
  glyphs: Record<string, AtlasGlyph>;
}

export interface ReviewSummary {
  wpc_ratio_median: number;
  height_ratio_median: number;
  density_ratio_median: number;
  severity_counts: Record<string, number>;
}

export interface ReviewResult {
  log: string;
  png: string;
  summary: ReviewSummary | null;
  failures: unknown[];
}
