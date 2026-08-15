import type { RotoscopeOptions } from "../home/Rotoscope/algorithm";
import type { MarkerExperimentInput } from "./labAlgorithm";
import {
  ROTOSCOPE_LAB_WORKER_VERSION,
  type RotoscopeLabRenderRequest,
  type RotoscopeLabRenderSuccess,
  type RotoscopeLabWorkerResponse,
} from "./protocol";

interface PendingLabRender {
  image: HTMLImageElement;
  request: RotoscopeLabRenderRequest;
  resolve: (result: RotoscopeLabRenderSuccess | null) => void;
  reject: (error: Error) => void;
}

export interface RotoscopeLabClientRequest {
  image: HTMLImageElement;
  width: number;
  height: number;
  baseOptions: Partial<RotoscopeOptions>;
  experiment: MarkerExperimentInput;
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

const closeResult = (result: RotoscopeLabRenderSuccess): void => {
  result.outputBitmap?.close();
  result.diagnosticBitmap?.close();
};

export class RotoscopeLabClient {
  private worker: Worker | null;
  private nextId = 1;
  private active: PendingLabRender | null = null;
  private queued: PendingLabRender | null = null;

  constructor(workerFactory?: () => Worker) {
    if (typeof Worker === "undefined") {
      this.worker = null;
      return;
    }
    try {
      this.worker = workerFactory
        ? workerFactory()
        : new Worker("/rotoscope/lab-worker-v2.js", {
            name: "portfolio-rotoscope-lab",
          });
    } catch {
      this.worker = null;
      return;
    }
    this.worker.onmessage = (event: MessageEvent<RotoscopeLabWorkerResponse>) => {
      this.handleMessage(event.data);
    };
    this.worker.onerror = () => {
      const error = new Error("rotoscope lab worker crashed");
      this.active?.reject(error);
      this.queued?.reject(error);
      this.active = null;
      this.queued = null;
      this.worker?.terminate();
      this.worker = null;
    };
  }

  available(): boolean {
    return this.worker !== null;
  }

  render(
    input: RotoscopeLabClientRequest,
  ): Promise<RotoscopeLabRenderSuccess | null> {
    if (!this.worker) return Promise.resolve(null);
    const id = this.nextId;
    this.nextId += 1;
    const request: RotoscopeLabRenderRequest = {
      version: ROTOSCOPE_LAB_WORKER_VERSION,
      type: "render",
      id,
      sourceUrl: input.image.currentSrc || input.image.src,
      width: input.width,
      height: input.height,
      baseOptions: input.baseOptions,
      experiment: input.experiment,
    };

    return new Promise((resolve, reject) => {
      const pending = { image: input.image, request, resolve, reject };
      if (this.active) {
        this.queued?.resolve(null);
        this.queued = pending;
        return;
      }
      this.active = pending;
      this.postActive();
    });
  }

  dispose(): void {
    this.active?.resolve(null);
    this.queued?.resolve(null);
    this.active = null;
    this.queued = null;
    this.worker?.terminate();
    this.worker = null;
  }

  private postActive(): void {
    if (!this.worker || !this.active) return;
    try {
      this.worker.postMessage(this.active.request);
    } catch (error) {
      const failed = this.active;
      this.active = null;
      failed.reject(error instanceof Error ? error : new Error(String(error)));
      this.dispatchQueued();
    }
  }

  private dispatchQueued(): void {
    if (!this.queued || !this.worker) return;
    this.active = this.queued;
    this.queued = null;
    this.postActive();
  }

  private handleMessage(message: RotoscopeLabWorkerResponse): void {
    const active = this.active;
    if (!active || active.request.id !== message.id) {
      if (message.type === "result") closeResult(message);
      return;
    }

    if (message.type === "needs-pixels") {
      try {
        const pixelsBuffer = extractPixels(
          active.image,
          active.request.width,
          active.request.height,
        );
        const fallbackRequest = {
          ...active.request,
          pixelsBuffer,
        };
        this.worker?.postMessage(fallbackRequest, [pixelsBuffer]);
      } catch (error) {
        this.active = null;
        active.reject(error instanceof Error ? error : new Error(String(error)));
        this.dispatchQueued();
      }
      return;
    }

    this.active = null;
    if (message.type === "error") {
      active.reject(new Error(message.message));
      this.dispatchQueued();
      return;
    }

    if (this.queued) {
      closeResult(message);
      active.resolve(null);
    } else {
      active.resolve(message);
    }
    this.dispatchQueued();
  }
}
