import type { RotoscopeOptions } from "./algorithm";

export const ROTOSCOPE_WORKER_VERSION = 2 as const;

export interface RotoscopeRenderRequest {
  version: typeof ROTOSCOPE_WORKER_VERSION;
  type: "render";
  id: number;
  sourceUrl?: string;
  pixelsBuffer?: ArrayBuffer;
  width: number;
  height: number;
  options: Partial<RotoscopeOptions>;
}

export interface RotoscopeTimings {
  decodeAndResizeMs: number;
  wasmLoadMs: number;
  focusMapMs: number;
  pipelineMs: number;
  paintMs: number;
  totalMs: number;
}

export interface RotoscopeRenderSuccess {
  version: typeof ROTOSCOPE_WORKER_VERSION;
  type: "result";
  id: number;
  width: number;
  height: number;
  markerCount: number;
  tierCounts: { face: number; body: number; background: number };
  path: "wasm-scalar" | "scalar-worker";
  timings: RotoscopeTimings;
  bitmap?: ImageBitmap;
  pixelsBuffer?: ArrayBuffer;
}

export interface RotoscopeNeedsPixels {
  version: typeof ROTOSCOPE_WORKER_VERSION;
  type: "needs-pixels";
  id: number;
}

export interface RotoscopeWorkerError {
  version: typeof ROTOSCOPE_WORKER_VERSION;
  type: "error";
  id: number;
  message: string;
}

export type RotoscopeWorkerResponse =
  | RotoscopeRenderSuccess
  | RotoscopeNeedsPixels
  | RotoscopeWorkerError;
