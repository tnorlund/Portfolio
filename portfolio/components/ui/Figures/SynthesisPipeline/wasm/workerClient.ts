/**
 * Main-thread client for the SynthesisPipeline pixel worker.
 * Falls back to null when Workers are unavailable (SSR / jsdom).
 */

export type WorkerKnockOutResult = {
  pixels: Uint8ClampedArray;
  via: "worker";
};

export type WorkerStampResult = {
  pixels: Uint8ClampedArray;
  via: "worker";
};

type Pending = {
  resolve: (value: ArrayBuffer) => void;
  reject: (reason: Error) => void;
};

const WORKER_URL = "/wasm/synthesis_pipeline_worker.js";

let worker: Worker | null | undefined;
let nextId = 1;
const pending = new Map<number, Pending>();

const canUseWorker = (): boolean =>
  typeof Worker !== "undefined" && typeof window !== "undefined";

const ensureWorker = (): Worker | null => {
  if (worker !== undefined) {
    return worker;
  }
  if (!canUseWorker()) {
    worker = null;
    return null;
  }
  try {
    const instance = new Worker(WORKER_URL);
    instance.onmessage = (event: MessageEvent) => {
      const data = event.data as {
        id: number;
        ok: boolean;
        buffer?: ArrayBuffer;
        error?: string;
      };
      const entry = pending.get(data.id);
      if (!entry) {
        return;
      }
      pending.delete(data.id);
      if (data.ok && data.buffer) {
        entry.resolve(data.buffer);
      } else {
        entry.reject(new Error(data.error || "worker failed"));
      }
    };
    instance.onerror = () => {
      // Disable worker after a hard failure; callers fall back to main thread.
      worker = null;
      pending.forEach((p) => p.reject(new Error("worker crashed")));
      pending.clear();
    };
    worker = instance;
    return instance;
  } catch {
    worker = null;
    return null;
  }
};

const requestBuffer = (
  instance: Worker,
  message: Record<string, unknown>,
  transfer: ArrayBuffer[],
): Promise<ArrayBuffer> => {
  const id = nextId;
  nextId += 1;
  return new Promise<ArrayBuffer>((resolve, reject) => {
    pending.set(id, { resolve, reject });
    instance.postMessage({ ...message, id }, transfer);
  });
};

export const knockOutInWorker = async (
  pixels: Uint8ClampedArray,
): Promise<WorkerKnockOutResult | null> => {
  const instance = ensureWorker();
  if (!instance) {
    return null;
  }
  try {
    // Transfer a copy so the caller's buffer stays usable on failure paths.
    const copy = pixels.slice().buffer;
    const buffer = await requestBuffer(
      instance,
      { type: "knockOut", buffer: copy },
      [copy],
    );
    return { pixels: new Uint8ClampedArray(buffer), via: "worker" };
  } catch {
    return null;
  }
};

export const stampThermalInWorker = async (params: {
  width: number;
  height: number;
  points: Float32Array;
  count: number;
  radius: number;
  red: number;
  green: number;
  blue: number;
}): Promise<WorkerStampResult | null> => {
  const instance = ensureWorker();
  if (!instance) {
    return null;
  }
  try {
    const pointsCopy = params.points.slice(0, params.count * 2).buffer;
    const buffer = await requestBuffer(
      instance,
      {
        type: "stampThermal",
        width: params.width,
        height: params.height,
        count: params.count,
        radius: params.radius,
        red: params.red,
        green: params.green,
        blue: params.blue,
        pointsBuffer: pointsCopy,
      },
      [pointsCopy],
    );
    return { pixels: new Uint8ClampedArray(buffer), via: "worker" };
  } catch {
    return null;
  }
};

/** Test-only: reset memoized worker. */
export const __resetWorkerClientForTests = (): void => {
  if (worker) {
    worker.terminate();
  }
  worker = undefined;
  pending.clear();
};
