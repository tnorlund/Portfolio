/// <reference lib="webworker" />

import {
  MAX_ROTOSCOPE_DIMENSION,
  MAX_ROTOSCOPE_PIXELS,
  normalizeRotoscopeOptions,
  runRotoscope,
} from "./algorithm";
import {
  PORTRAIT_PERSON_MASK_PATH,
  PORTRAIT_PRIMARY_FEATURES,
  decodePortraitPersonMask,
} from "./portraitReveal";
import {
  createBasinRevealMap,
  type BasinRevealSemantics,
} from "./reveal";
import {
  ROTOSCOPE_WORKER_VERSION,
  type RotoscopeRenderRequest,
  type RotoscopeRenderSuccess,
  type RotoscopeTimings,
  type RotoscopeWorkerResponse,
} from "./workerProtocol";
import { runRotoscopeWasm } from "./wasm";

type WorkerTransfer = ArrayBuffer;

interface RotoscopeWorkerScope {
  onmessage: ((event: MessageEvent<RotoscopeRenderRequest>) => void) | null;
  postMessage(message: RotoscopeWorkerResponse, transfer?: WorkerTransfer[]): void;
}

const workerScope = self as unknown as RotoscopeWorkerScope;

// Production medians on the same 480x360 pixels show Firefox's scalar Wasm
// backend is materially slower than its optimized JavaScript worker. Keep the
// faster oracle there; Chromium and WebKit use Wasm. Revisit with the browser
// benchmark when Firefox's backend changes.
const shouldAttemptWasm = (): boolean =>
  typeof navigator === "undefined" || !/\bFirefox\//.test(navigator.userAgent);

interface CachedSource {
  key: string;
  pixels: Uint8ClampedArray;
}

let cachedSource: CachedSource | null = null;
let pendingRequest: RotoscopeRenderRequest | null = null;
let draining = false;
let latestRequestId = 0;
let revealSemanticsPromise: Promise<BasinRevealSemantics> | null = null;

const loadRevealSemantics = (): Promise<BasinRevealSemantics> => {
  if (revealSemanticsPromise) return revealSemanticsPromise;
  revealSemanticsPromise = fetch(PORTRAIT_PERSON_MASK_PATH, {
    cache: "force-cache",
  })
    .then(async (response) => {
      if (!response.ok) throw new Error("portrait person mask is unavailable");
      return {
        primaryFeatures: PORTRAIT_PRIMARY_FEATURES,
        personMask: decodePortraitPersonMask(await response.json()),
      };
    })
    .catch(() => ({ primaryFeatures: PORTRAIT_PRIMARY_FEATURES }));
  return revealSemanticsPromise;
};

const post = (
  response: RotoscopeWorkerResponse,
  transfer: WorkerTransfer[] = [],
): void => {
  workerScope.postMessage(response, transfer);
};

const decodeSource = async (
  sourceUrl: string,
  width: number,
  height: number,
): Promise<{ pixels: Uint8ClampedArray; elapsedMs: number }> => {
  const key = `${sourceUrl}:${width}x${height}`;
  if (cachedSource?.key === key) {
    return { pixels: cachedSource.pixels, elapsedMs: 0 };
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
    throw new Error("2D canvas is unavailable in the worker");
  }
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  const pixels = context.getImageData(0, 0, width, height).data;
  cachedSource = { key, pixels };
  return { pixels, elapsedMs: performance.now() - startedAt };
};

const pixelsForRequest = async (
  request: RotoscopeRenderRequest,
): Promise<{ pixels: Uint8ClampedArray; elapsedMs: number }> => {
  if (request.pixelsBuffer) {
    const pixels = new Uint8ClampedArray(request.pixelsBuffer);
    if (request.sourceUrl) {
      cachedSource = {
        key: `${request.sourceUrl}:${request.width}x${request.height}`,
        pixels,
      };
    }
    return {
      pixels,
      elapsedMs: 0,
    };
  }
  if (!request.sourceUrl) throw new Error("missing portrait source");
  return decodeSource(request.sourceUrl, request.width, request.height);
};

const render = async (request: RotoscopeRenderRequest): Promise<void> => {
  if (request.version !== ROTOSCOPE_WORKER_VERSION) {
    throw new Error("unsupported worker protocol");
  }
  const pixelCount = request.width * request.height;
  if (
    !Number.isInteger(request.width) ||
    !Number.isInteger(request.height) ||
    request.width <= 0 ||
    request.height <= 0 ||
    request.width > MAX_ROTOSCOPE_DIMENSION ||
    request.height > MAX_ROTOSCOPE_DIMENSION ||
    !Number.isSafeInteger(pixelCount) ||
    pixelCount > MAX_ROTOSCOPE_PIXELS
  ) {
    throw new Error("invalid rotoscope dimensions");
  }
  if (request.pixelsBuffer && request.pixelsBuffer.byteLength !== pixelCount * 4) {
    throw new Error("RGBA byte length does not match the dimensions");
  }
  const totalStartedAt = performance.now();
  const revealSemantics = loadRevealSemantics();
  let decoded: { pixels: Uint8ClampedArray; elapsedMs: number };
  try {
    decoded = await pixelsForRequest(request);
  } catch (error) {
    if (error instanceof Error && error.message === "needs-pixels") {
      post({
        version: ROTOSCOPE_WORKER_VERSION,
        type: "needs-pixels",
        id: request.id,
      });
      return;
    }
    throw error;
  }

  const accelerated = shouldAttemptWasm()
    ? await runRotoscopeWasm(
        decoded.pixels,
        request.width,
        request.height,
        request.options,
      )
    : null;
  let result;
  let path: RotoscopeRenderSuccess["path"];
  let pipelineMs: number;
  let wasmLoadMs = 0;
  let focusMapMs = 0;
  if (accelerated) {
    result = accelerated.result;
    path = accelerated.path;
    pipelineMs = accelerated.pipelineMs;
    wasmLoadMs = accelerated.loadMs;
    focusMapMs = accelerated.focusMapMs;
  } else {
    const pipelineStartedAt = performance.now();
    result = runRotoscope(
      decoded.pixels,
      request.width,
      request.height,
      request.options,
    );
    pipelineMs = performance.now() - pipelineStartedAt;
    path = "scalar-worker";
  }
  const paintStartedAt = performance.now();
  const normalizedOptions = normalizeRotoscopeOptions(request.options, pixelCount);
  const reveal = createBasinRevealMap(
    result.pixels,
    request.width,
    request.height,
    normalizedOptions.focus,
    undefined,
    await revealSemantics,
  );
  const paintMs = performance.now() - paintStartedAt;

  // A newer request superseded this result while decode or compute was active.
  if (request.id !== latestRequestId) {
    return;
  }

  const timings: RotoscopeTimings = {
    decodeAndResizeMs: decoded.elapsedMs,
    wasmLoadMs,
    focusMapMs,
    pipelineMs,
    paintMs,
    totalMs: performance.now() - totalStartedAt,
  };
  const response: RotoscopeRenderSuccess = {
    version: ROTOSCOPE_WORKER_VERSION,
    type: "result",
    id: request.id,
    width: request.width,
    height: request.height,
    markerCount: result.markerCount,
    tierCounts: result.tierCounts,
    path,
    timings,
    pixelsBuffer: result.pixels.buffer as ArrayBuffer,
    revealPhasesBuffer: reveal.phases.buffer as ArrayBuffer,
    revealPhaseCount: reveal.phaseCount,
    revealBasinCount: reveal.basinCount,
  };
  post(response, [response.pixelsBuffer, response.revealPhasesBuffer]);
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
        version: ROTOSCOPE_WORKER_VERSION,
        type: "error",
        id: request.id,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }
  draining = false;
};

workerScope.onmessage = (event: MessageEvent<RotoscopeRenderRequest>) => {
  const request = event.data;
  if (!request || request.type !== "render" || !Number.isInteger(request.id)) {
    return;
  }
  latestRequestId = Math.max(latestRequestId, request.id);
  pendingRequest = request;
  void drain();
};

export {};
