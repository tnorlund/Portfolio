import type { RotoscopeOptions } from "../home/Rotoscope/algorithm";
import type {
  MarkerExperimentInput,
  MarkerExperimentOptions,
} from "./labAlgorithm";

export const ROTOSCOPE_LAB_WORKER_VERSION = 1 as const;

export interface RotoscopeLabRenderRequest {
  version: typeof ROTOSCOPE_LAB_WORKER_VERSION;
  type: "render";
  id: number;
  sourceUrl?: string;
  pixelsBuffer?: ArrayBuffer;
  width: number;
  height: number;
  baseOptions: Partial<RotoscopeOptions>;
  experiment: MarkerExperimentInput;
}

export interface RotoscopeLabTimings {
  decodeAndResizeMs: number;
  prepareMs: number;
  noiseMs: number;
  selectionMs: number;
  watershedMs: number;
  colorMs: number;
  diagnosticMs: number;
  paintMs: number;
  totalMs: number;
}

export interface RotoscopeLabRenderSuccess {
  version: typeof ROTOSCOPE_LAB_WORKER_VERSION;
  type: "result";
  id: number;
  width: number;
  height: number;
  markerCount: number;
  tierCounts: { face: number; body: number; background: number };
  markerDigest: string;
  labelDigest: string;
  path: "scalar-lab";
  normalizedExperiment: MarkerExperimentOptions;
  normalizedBaseOptions: RotoscopeOptions;
  timings: RotoscopeLabTimings;
  outputBitmap?: ImageBitmap;
  diagnosticBitmap?: ImageBitmap;
  outputPixelsBuffer?: ArrayBuffer;
  diagnosticPixelsBuffer?: ArrayBuffer;
  markerIndicesBuffer: ArrayBuffer;
}

export interface RotoscopeLabNeedsPixels {
  version: typeof ROTOSCOPE_LAB_WORKER_VERSION;
  type: "needs-pixels";
  id: number;
}

export interface RotoscopeLabWorkerError {
  version: typeof ROTOSCOPE_LAB_WORKER_VERSION;
  type: "error";
  id: number;
  message: string;
}

export type RotoscopeLabWorkerResponse =
  | RotoscopeLabRenderSuccess
  | RotoscopeLabNeedsPixels
  | RotoscopeLabWorkerError;
