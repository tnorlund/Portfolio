import type {
  CompileResult,
  FontBundle,
  FontSource,
  GlyphSource,
  RasterResult,
  ReviewResult,
} from "./types";

const FONT = "sprouts";

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}
async function jsend<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${url} → ${r.status} ${text}`);
  return (text ? JSON.parse(text) : {}) as T;
}

export const api = {
  font: FONT,
  getFont: () => jget<FontBundle>(`/api/font/${FONT}`),
  putGlyph: (cp: number, g: GlyphSource) =>
    jsend<{ ok: boolean }>("PUT", `/api/font/${FONT}/glyph/${cp}`, g),
  putFont: (f: FontSource) =>
    jsend<{ ok: boolean }>("PUT", `/api/font/${FONT}/font`, f),
  adoptTrace: (cp: number) =>
    jsend<{ ok: boolean }>("POST", `/api/font/${FONT}/glyph/${cp}/adopt-trace`),
  raster: (glyph: GlyphSource, params: FontSource["params"], refCap: number) =>
    jsend<RasterResult>("POST", `/api/raster`, { glyph, params, refCap }),
  compile: () => jsend<CompileResult>("POST", `/api/compile/${FONT}`),
  review: () => jsend<ReviewResult>("POST", `/api/review/${FONT}`),
  trace: (chars?: string, force?: boolean) =>
    jsend<{ log: string }>("POST", `/api/trace/${FONT}`, { chars, force }),
  sampleUrl: (cp: number, mode: "median" | "index" = "median", i = 0) =>
    `/api/samples/${FONT}/${cp}.png?mode=${mode}&i=${i}`,
};

// Fetch a sample PNG plus its positioning headers.
export interface SampleMeta {
  url: string;
  refCap: number;
  baselineRow: number;
  n: number;
}
export async function fetchSampleMeta(
  cp: number,
  mode: "median" | "index" = "median",
  i = 0,
): Promise<SampleMeta> {
  const url = api.sampleUrl(cp, mode, i);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`sample ${cp} → ${r.status}`);
  return {
    url,
    refCap: Number(r.headers.get("x-ref-cap") || 40),
    baselineRow: Number(r.headers.get("x-baseline-row") || 92),
    n: Number(r.headers.get("x-n") || 0),
  };
}
