import { RotoscopeWorkerClient } from "./workerClient";
import {
  ROTOSCOPE_WORKER_VERSION,
  type RotoscopeRenderSuccess,
  type RotoscopeWorkerResponse,
} from "./workerProtocol";

class FakeWorker {
  onmessage: ((event: MessageEvent<RotoscopeWorkerResponse>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  postMessage = jest.fn();
  terminate = jest.fn();

  emit(message: RotoscopeWorkerResponse): void {
    this.onmessage?.({ data: message } as MessageEvent<RotoscopeWorkerResponse>);
  }

  crash(): void {
    this.onerror?.(new Event("error"));
  }
}

const originalWorker = global.Worker;

const installWorkerGlobal = (value: typeof Worker | undefined): void => {
  Object.defineProperty(global, "Worker", {
    configurable: true,
    writable: true,
    value,
  });
};

const image = (): HTMLImageElement => {
  const element = document.createElement("img");
  element.src = "https://example.test/portrait.jpg";
  return element;
};

const success = (
  id: number,
  bitmap?: ImageBitmap,
): RotoscopeRenderSuccess => ({
  version: ROTOSCOPE_WORKER_VERSION,
  type: "result",
  id,
  width: 4,
  height: 3,
  markerCount: 2,
  tierCounts: { face: 1, body: 1, background: 0 },
  path: "wasm-scalar",
  timings: {
    decodeAndResizeMs: 1,
    wasmLoadMs: 0,
    focusMapMs: 0,
    pipelineMs: 2,
    paintMs: 3,
    totalMs: 6,
  },
  bitmap,
});

beforeEach(() => {
  installWorkerGlobal(FakeWorker as unknown as typeof Worker);
  jest.clearAllMocks();
});

afterAll(() => {
  installWorkerGlobal(originalWorker);
});

test("is unavailable when Worker is absent or construction fails", async () => {
  installWorkerGlobal(undefined);
  const unavailable = new RotoscopeWorkerClient();
  expect(unavailable.available()).toBe(false);
  await expect(
    unavailable.render({ image: image(), width: 4, height: 3, options: {} }),
  ).resolves.toBeNull();

  installWorkerGlobal(FakeWorker as unknown as typeof Worker);
  const failed = new RotoscopeWorkerClient(() => {
    throw new Error("constructor failed");
  });
  expect(failed.available()).toBe(false);
});

test("reposts a needs-pixels request with the pixel buffer transferred", () => {
  const worker = new FakeWorker();
  const client = new RotoscopeWorkerClient(() => worker as unknown as Worker);
  const pixels = new Uint8ClampedArray(4 * 3 * 4);
  const context = {
    drawImage: jest.fn(),
    getImageData: jest.fn(() => ({ data: pixels })),
  };
  (HTMLCanvasElement.prototype.getContext as jest.Mock).mockReturnValue(context);

  void client.render({ image: image(), width: 4, height: 3, options: {} });
  worker.emit({
    version: ROTOSCOPE_WORKER_VERSION,
    type: "needs-pixels",
    id: 1,
  });

  expect(context.drawImage).toHaveBeenCalledWith(expect.any(HTMLImageElement), 0, 0, 4, 3);
  expect(context.getImageData).toHaveBeenCalledWith(0, 0, 4, 3);
  expect(worker.postMessage).toHaveBeenCalledTimes(2);
  const [message, transfer] = worker.postMessage.mock.calls[1] as [
    { sourceUrl?: string; pixelsBuffer: ArrayBuffer },
    ArrayBuffer[],
  ];
  expect(message.sourceUrl).toBeUndefined();
  expect(message.pixelsBuffer).toBe(pixels.buffer);
  expect(transfer).toEqual([pixels.buffer]);
});

test("closes result bitmaps for stale and unknown requests", async () => {
  const worker = new FakeWorker();
  const client = new RotoscopeWorkerClient(() => worker as unknown as Worker);
  const first = client.render({ image: image(), width: 4, height: 3, options: {} });
  const second = client.render({ image: image(), width: 4, height: 3, options: {} });
  await expect(first).resolves.toBeNull();

  const staleClose = jest.fn();
  const unknownClose = jest.fn();
  worker.emit(success(1, { close: staleClose } as unknown as ImageBitmap));
  worker.emit(success(404, { close: unknownClose } as unknown as ImageBitmap));

  expect(staleClose).toHaveBeenCalledTimes(1);
  expect(unknownClose).toHaveBeenCalledTimes(1);
  const current = success(2);
  worker.emit(current);
  await expect(second).resolves.toBe(current);
});

test("rejects pending work and disables the client after a worker crash", async () => {
  const worker = new FakeWorker();
  const client = new RotoscopeWorkerClient(() => worker as unknown as Worker);
  const pending = client.render({ image: image(), width: 4, height: 3, options: {} });
  const rejected = expect(pending).rejects.toThrow("rotoscope worker crashed");

  worker.crash();

  await rejected;
  expect(worker.terminate).toHaveBeenCalledTimes(1);
  expect(client.available()).toBe(false);
});

test("dispose resolves pending work, terminates once, and makes the client unavailable", async () => {
  const worker = new FakeWorker();
  const client = new RotoscopeWorkerClient(() => worker as unknown as Worker);
  const pending = client.render({ image: image(), width: 4, height: 3, options: {} });

  client.dispose();

  await expect(pending).resolves.toBeNull();
  expect(worker.terminate).toHaveBeenCalledTimes(1);
  expect(client.available()).toBe(false);
  client.dispose();
  expect(worker.terminate).toHaveBeenCalledTimes(1);
});
