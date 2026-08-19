/// <reference lib="webworker" />

import {
  MAX_ROTOSCOPE_PIXELS,
  normalizeRotoscopeOptions,
} from "../home/Rotoscope/algorithm";
import {
  createNoiseField,
  experimentNoiseCacheKey,
  normalizeMarkerExperiment,
  prepareExperimentStages,
  runRotoscopeExperiment,
  type PreparedExperimentStages,
} from "./labAlgorithm";
import {
  ROTOSCOPE_LAB_WORKER_VERSION,
  type RotoscopeLabRenderRequest,
  type RotoscopeLabRenderSuccess,
  type RotoscopeLabTimings,
  type RotoscopeLabWorkerResponse,
} from "./protocol";
import { LabSourceCache, labSourceCacheKey } from "./sourceCache";
import {
  loadVisionPortraitArtifacts,
  primaryFaceVisionFeatures,
  type VisionPortraitArtifacts,
} from "./vision";

type WorkerTransfer = ArrayBuffer | ImageBitmap;

interface LabWorkerScope {
  onmessage: ((event: MessageEvent<RotoscopeLabRenderRequest>) => void) | null;
  postMessage(message: RotoscopeLabWorkerResponse, transfer?: WorkerTransfer[]): void;
}

interface CachedStages {
  key: string;
  blurRadius: number;
  stages: PreparedExperimentStages;
}

interface CachedNoise {
  key: string;
  field: Float32Array | null;
}

const LAB_MAX_WIDTH = 480;
const LAB_MAX_HEIGHT = 360;
const LAB_MAX_PIXELS = Math.min(MAX_ROTOSCOPE_PIXELS, LAB_MAX_WIDTH * LAB_MAX_HEIGHT);
const workerScope = self as unknown as LabWorkerScope;

const sourceCache = new LabSourceCache();
let cachedStages: CachedStages | null = null;
let cachedNoise: CachedNoise | null = null;
let pendingRequest: RotoscopeLabRenderRequest | null = null;
let latestRequestId = 0;
let draining = false;
let cachedVision: VisionPortraitArtifacts | null = null;
let visionLoadAttempted = false;
let visionLoadError: string | null = null;

const visionArtifacts = async (): Promise<VisionPortraitArtifacts | null> => {
  if (cachedVision) return cachedVision;
  if (visionLoadAttempted) return null;
  visionLoadAttempted = true;
  try {
    cachedVision = await loadVisionPortraitArtifacts();
    return cachedVision;
  } catch (error) {
    visionLoadError = error instanceof Error ? error.message : String(error);
    return null;
  }
};

const post = (
  response: RotoscopeLabWorkerResponse,
  transfer: WorkerTransfer[] = [],
): void => {
  workerScope.postMessage(response, transfer);
};

const decodeSource = async (
  sourceUrl: string,
  width: number,
  height: number,
): Promise<{ key: string; pixels: Uint8ClampedArray; elapsedMs: number }> => {
  const key = labSourceCacheKey(sourceUrl, width, height);
  const cachedSource = sourceCache.get(sourceUrl, width, height);
  if (cachedSource) {
    return { key, pixels: cachedSource.pixels, elapsedMs: 0 };
  }
  if (
    typeof OffscreenCanvas === "undefined" ||
    typeof createImageBitmap !== "function"
  ) {
    throw new Error("needs-pixels");
  }
  const startedAt = performance.now();
  const response = await fetch(sourceUrl, { cache: "force-cache" });
  if (!response.ok) throw new Error(`portrait request failed (${response.status})`);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob, {
    resizeWidth: width,
    resizeHeight: height,
    resizeQuality: "high",
  });
  const canvas = new OffscreenCanvas(width, height);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) {
    bitmap.close();
    throw new Error("2D canvas is unavailable in the lab worker");
  }
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  const pixels = context.getImageData(0, 0, width, height).data;
  const stored = sourceCache.store(sourceUrl, width, height, pixels);
  if (stored.changed) cachedStages = null;
  return { key, pixels: stored.entry.pixels, elapsedMs: performance.now() - startedAt };
};

const pixelsForRequest = async (
  request: RotoscopeLabRenderRequest,
): Promise<{ key: string; pixels: Uint8ClampedArray; elapsedMs: number }> => {
  if (request.pixelsBuffer) {
    const sourceKey = request.sourceUrl ?? `pixels:${request.id}`;
    const key = labSourceCacheKey(sourceKey, request.width, request.height);
    const pixels = new Uint8ClampedArray(request.pixelsBuffer);
    const stored = sourceCache.store(sourceKey, request.width, request.height, pixels);
    if (stored.changed) cachedStages = null;
    return {
      key,
      pixels: stored.entry.pixels,
      elapsedMs: 0,
    };
  }
  if (!request.sourceUrl) throw new Error("missing portrait source");
  return decodeSource(request.sourceUrl, request.width, request.height);
};

const paintPixels = (
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
): { bitmap?: ImageBitmap; pixelsBuffer?: ArrayBuffer } => {
  if (typeof OffscreenCanvas === "undefined") {
    return { pixelsBuffer: pixels.buffer as ArrayBuffer };
  }
  const canvas = new OffscreenCanvas(width, height);
  const context = canvas.getContext("2d");
  if (!context) return { pixelsBuffer: pixels.buffer as ArrayBuffer };
  const imageData = context.createImageData(width, height);
  imageData.data.set(pixels);
  context.putImageData(imageData, 0, 0);
  return { bitmap: canvas.transferToImageBitmap() };
};

const render = async (request: RotoscopeLabRenderRequest): Promise<void> => {
  if (request.version !== ROTOSCOPE_LAB_WORKER_VERSION) {
    throw new Error("unsupported lab worker protocol");
  }
  const pixelCount = request.width * request.height;
  if (
    !Number.isInteger(request.width) ||
    !Number.isInteger(request.height) ||
    request.width <= 0 ||
    request.height <= 0 ||
    request.width > LAB_MAX_WIDTH ||
    request.height > LAB_MAX_HEIGHT ||
    !Number.isSafeInteger(pixelCount) ||
    pixelCount > LAB_MAX_PIXELS
  ) {
    throw new Error("invalid rotoscope lab dimensions");
  }
  if (request.pixelsBuffer && request.pixelsBuffer.byteLength !== pixelCount * 4) {
    throw new Error("RGBA byte length does not match the dimensions");
  }

  const totalStartedAt = performance.now();
  let decoded: Awaited<ReturnType<typeof pixelsForRequest>>;
  try {
    decoded = await pixelsForRequest(request);
  } catch (error) {
    if (error instanceof Error && error.message === "needs-pixels") {
      post({
        version: ROTOSCOPE_LAB_WORKER_VERSION,
        type: "needs-pixels",
        id: request.id,
      });
      return;
    }
    throw error;
  }

  const normalizedBase = normalizeRotoscopeOptions(request.baseOptions, pixelCount);
  const normalizedExperiment = normalizeMarkerExperiment(request.experiment);
  const includeFeatures =
    normalizedExperiment.strategy === "features" ||
    (normalizedExperiment.strategy === "hybrid" &&
      normalizedExperiment.hybridRadialWeight < 1);
  const vision =
    normalizedExperiment.strategy === "vision"
      ? await visionArtifacts()
      : null;

  const prepareStartedAt = performance.now();
  const stageKey = `${decoded.key}:${request.width}x${request.height}`;
  if (
    !cachedStages ||
    cachedStages.key !== stageKey ||
    cachedStages.blurRadius !== normalizedBase.blurRadius ||
    (includeFeatures && !cachedStages.stages.featureScores)
  ) {
    cachedStages = {
      key: stageKey,
      blurRadius: normalizedBase.blurRadius,
      stages: prepareExperimentStages(
        decoded.pixels,
        request.width,
        request.height,
        normalizedBase.blurRadius,
        includeFeatures,
      ),
    };
  }
  const prepareMs = performance.now() - prepareStartedAt;

  const noiseStartedAt = performance.now();
  const nextNoiseKey = experimentNoiseCacheKey(
    request.width,
    request.height,
    normalizedExperiment,
  );
  if (!cachedNoise || cachedNoise.key !== nextNoiseKey) {
    cachedNoise = {
      key: nextNoiseKey,
      field: createNoiseField(
        request.width,
        request.height,
        normalizedExperiment,
      ),
    };
  }
  const noiseMs = performance.now() - noiseStartedAt;

  const result = runRotoscopeExperiment(
    cachedStages.stages,
    normalizedBase,
    normalizedExperiment,
    cachedNoise.field,
    vision ?? undefined,
  );
  const paintStartedAt = performance.now();
  const output = paintPixels(result.pixels, request.width, request.height);
  const diagnostic = paintPixels(
    result.diagnosticPixels,
    request.width,
    request.height,
  );
  const paintMs = performance.now() - paintStartedAt;

  if (request.id !== latestRequestId) {
    output.bitmap?.close();
    diagnostic.bitmap?.close();
    return;
  }

  const timings: RotoscopeLabTimings = {
    decodeAndResizeMs: decoded.elapsedMs,
    prepareMs,
    noiseMs,
    selectionMs: result.timings.selectionMs,
    watershedMs: result.timings.watershedMs,
    colorMs: result.timings.colorMs,
    diagnosticMs: result.timings.diagnosticMs,
    paintMs,
    totalMs: performance.now() - totalStartedAt,
  };
  const markerIndicesBuffer = result.markerIndices.buffer as ArrayBuffer;
  const response: RotoscopeLabRenderSuccess = {
    version: ROTOSCOPE_LAB_WORKER_VERSION,
    type: "result",
    id: request.id,
    width: request.width,
    height: request.height,
    markerCount: result.markerCount,
    tierCounts: result.tierCounts,
    markerDigest: result.markerDigest,
    labelDigest: result.labelDigest,
    vision:
      normalizedExperiment.strategy === "vision"
        ? {
            available: vision !== null,
            featureCount: result.visionFeatureCount,
            markerCount: result.visionMarkerCount,
            faceLandmarkCount:
              vision?.features.filter((feature) => feature.kind === "face-landmark")
                .length ?? 0,
            captureQuality: vision?.primaryFace.captureQuality ?? null,
            ...(visionLoadError ? { message: visionLoadError } : {}),
          }
        : undefined,
    visionFeatures: vision ? primaryFaceVisionFeatures(vision) : undefined,
    path: "scalar-lab",
    normalizedExperiment: result.normalizedExperiment,
    normalizedBaseOptions: result.normalizedBaseOptions,
    timings,
    outputBitmap: output.bitmap,
    diagnosticBitmap: diagnostic.bitmap,
    outputPixelsBuffer: output.pixelsBuffer,
    diagnosticPixelsBuffer: diagnostic.pixelsBuffer,
    markerIndicesBuffer,
  };
  const transfer: WorkerTransfer[] = [markerIndicesBuffer];
  if (output.bitmap) transfer.push(output.bitmap);
  if (diagnostic.bitmap) transfer.push(diagnostic.bitmap);
  if (output.pixelsBuffer) transfer.push(output.pixelsBuffer);
  if (diagnostic.pixelsBuffer) transfer.push(diagnostic.pixelsBuffer);
  post(response, transfer);
};

const drain = async (): Promise<void> => {
  if (draining) return;
  draining = true;
  while (pendingRequest) {
    const request = pendingRequest;
    pendingRequest = null;
    try {
      await render(request);
    } catch (error) {
      post({
        version: ROTOSCOPE_LAB_WORKER_VERSION,
        type: "error",
        id: request.id,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }
  draining = false;
};

workerScope.onmessage = (event: MessageEvent<RotoscopeLabRenderRequest>) => {
  const request = event.data;
  if (!request || request.type !== "render" || !Number.isInteger(request.id)) return;
  latestRequestId = Math.max(latestRequestId, request.id);
  pendingRequest = request;
  void drain();
};

export {};
