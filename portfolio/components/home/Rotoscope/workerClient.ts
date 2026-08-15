import type { RotoscopeOptions } from "./algorithm";
import {
  ROTOSCOPE_WORKER_VERSION,
  type RotoscopeRenderRequest,
  type RotoscopeRenderSuccess,
  type RotoscopeWorkerResponse,
} from "./workerProtocol";

interface PendingRender {
  image: HTMLImageElement;
  resolve: (result: RotoscopeRenderSuccess | null) => void;
  reject: (error: Error) => void;
  request: RotoscopeRenderRequest;
}

export interface RenderPortraitRequest {
  image: HTMLImageElement;
  width: number;
  height: number;
  options: Partial<RotoscopeOptions>;
}

const extractPixels = (
  image: HTMLImageElement,
  width: number,
  height: number,
): ArrayBuffer => {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("2D canvas is unavailable");
  context.drawImage(image, 0, 0, width, height);
  return context.getImageData(0, 0, width, height).data.buffer as ArrayBuffer;
};

export class RotoscopeWorkerClient {
  private worker: Worker | null;
  private nextId = 1;
  private latestId = 0;
  private pending = new Map<number, PendingRender>();

  constructor(workerFactory?: () => Worker) {
    if (typeof Worker === "undefined") {
      this.worker = null;
      return;
    }
    try {
      this.worker = workerFactory
        ? workerFactory()
        : new Worker("/rotoscope/worker-v1.js", {
            name: "portfolio-rotoscope",
          });
    } catch {
      this.worker = null;
      return;
    }
    this.worker.onmessage = (event: MessageEvent<RotoscopeWorkerResponse>) => {
      this.handleMessage(event.data);
    };
    this.worker.onerror = () => {
      const error = new Error("rotoscope worker crashed");
      this.pending.forEach((entry) => entry.reject(error));
      this.pending.clear();
      this.worker?.terminate();
      this.worker = null;
    };
  }

  available(): boolean {
    return this.worker !== null;
  }

  render(request: RenderPortraitRequest): Promise<RotoscopeRenderSuccess | null> {
    if (!this.worker) return Promise.resolve(null);
    this.pending.forEach((entry, id) => {
      if (id < this.nextId) entry.resolve(null);
    });
    this.pending.clear();

    const id = this.nextId;
    this.nextId += 1;
    this.latestId = id;
    const message: RotoscopeRenderRequest = {
      version: ROTOSCOPE_WORKER_VERSION,
      type: "render",
      id,
      sourceUrl: request.image.currentSrc || request.image.src,
      width: request.width,
      height: request.height,
      options: request.options,
    };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { image: request.image, resolve, reject, request: message });
      try {
        this.worker?.postMessage(message);
      } catch (error) {
        this.pending.delete(id);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  dispose(): void {
    this.pending.forEach((entry) => entry.resolve(null));
    this.pending.clear();
    this.worker?.terminate();
    this.worker = null;
  }

  private handleMessage(message: RotoscopeWorkerResponse): void {
    const entry = this.pending.get(message.id);
    if (!entry) {
      if (message.type === "result") message.bitmap?.close();
      return;
    }
    if (message.type === "needs-pixels") {
      try {
        const pixelsBuffer = extractPixels(
          entry.image,
          entry.request.width,
          entry.request.height,
        );
        const fallbackRequest = { ...entry.request, sourceUrl: undefined, pixelsBuffer };
        this.worker?.postMessage(fallbackRequest, [pixelsBuffer]);
      } catch (error) {
        this.pending.delete(message.id);
        entry.reject(error instanceof Error ? error : new Error(String(error)));
      }
      return;
    }

    this.pending.delete(message.id);
    if (message.type === "error") {
      entry.reject(new Error(message.message));
      return;
    }
    if (message.id !== this.latestId) {
      message.bitmap?.close();
      entry.resolve(null);
      return;
    }
    entry.resolve(message);
  }
}
