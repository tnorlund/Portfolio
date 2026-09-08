import { normalizeRotoscopeOptions } from "../home/Rotoscope/algorithm";
import { RotoscopeLabClient } from "./client";
import { normalizeMarkerExperiment } from "./labAlgorithm";
import {
  ROTOSCOPE_LAB_WORKER_VERSION,
  type RotoscopeLabRenderSuccess,
  type RotoscopeLabWorkerResponse,
} from "./protocol";

class FakeWorker {
  onmessage: ((event: MessageEvent<RotoscopeLabWorkerResponse>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  postMessage = jest.fn();
  terminate = jest.fn();

  emit(message: RotoscopeLabWorkerResponse): void {
    this.onmessage?.({ data: message } as MessageEvent<RotoscopeLabWorkerResponse>);
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
  outputBitmap?: ImageBitmap,
  diagnosticBitmap?: ImageBitmap,
): RotoscopeLabRenderSuccess => ({
  version: ROTOSCOPE_LAB_WORKER_VERSION,
  type: "result",
  id,
  width: 4,
  height: 3,
  markerCount: 2,
  tierCounts: { face: 1, body: 1, background: 0 },
  markerDigest: "12345678",
  labelDigest: "90abcdef",
  path: "scalar-lab",
  normalizedExperiment: normalizeMarkerExperiment(),
  normalizedBaseOptions: normalizeRotoscopeOptions({}, 12),
  timings: {
    decodeAndResizeMs: 1,
    prepareMs: 2,
    noiseMs: 3,
    selectionMs: 4,
    watershedMs: 5,
    colorMs: 6,
    diagnosticMs: 7,
    paintMs: 8,
    totalMs: 36,
  },
  outputBitmap,
  diagnosticBitmap,
  markerIndicesBuffer: Uint32Array.from([1, 8]).buffer,
});

const request = () => ({
  image: image(),
  width: 4,
  height: 3,
  baseOptions: {},
  experiment: {},
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
  const unavailable = new RotoscopeLabClient();
  expect(unavailable.available()).toBe(false);
  await expect(unavailable.render(request())).resolves.toBeNull();

  installWorkerGlobal(FakeWorker as unknown as typeof Worker);
  const failed = new RotoscopeLabClient(() => {
    throw new Error("construction failed");
  });
  expect(failed.available()).toBe(false);
});

test("one-in-flight one-latest backpressure collapses a 100-event burst", async () => {
  const worker = new FakeWorker();
  const client = new RotoscopeLabClient(() => worker as unknown as Worker);
  const promises = Array.from({ length: 100 }, () => client.render(request()));

  expect(worker.postMessage).toHaveBeenCalledTimes(1);
  expect(worker.postMessage.mock.calls[0][0]).toMatchObject({ id: 1, version: 3 });

  const staleOutputClose = jest.fn();
  const staleDiagnosticClose = jest.fn();
  worker.emit(
    success(
      1,
      { close: staleOutputClose } as unknown as ImageBitmap,
      { close: staleDiagnosticClose } as unknown as ImageBitmap,
    ),
  );

  expect(staleOutputClose).toHaveBeenCalledTimes(1);
  expect(staleDiagnosticClose).toHaveBeenCalledTimes(1);
  expect(worker.postMessage).toHaveBeenCalledTimes(2);
  expect(worker.postMessage.mock.calls[1][0]).toMatchObject({ id: 100, version: 3 });

  const final = success(100);
  worker.emit(final);
  await expect(promises[0]).resolves.toBeNull();
  await expect(Promise.all(promises.slice(1, -1))).resolves.toEqual(
    Array.from({ length: 98 }, () => null),
  );
  await expect(promises[99]).resolves.toBe(final);
});

test("reposts a needs-pixels request with the exact transferable buffer", () => {
  const worker = new FakeWorker();
  const client = new RotoscopeLabClient(() => worker as unknown as Worker);
  const pixels = new Uint8ClampedArray(4 * 3 * 4);
  const context = {
    drawImage: jest.fn(),
    getImageData: jest.fn(() => ({ data: pixels })),
  };
  (HTMLCanvasElement.prototype.getContext as jest.Mock).mockReturnValue(context);

  void client.render(request());
  worker.emit({
    version: ROTOSCOPE_LAB_WORKER_VERSION,
    type: "needs-pixels",
    id: 1,
  });

  expect(worker.postMessage).toHaveBeenCalledTimes(2);
  const [message, transfer] = worker.postMessage.mock.calls[1] as [
    { sourceUrl?: string; pixelsBuffer: ArrayBuffer },
    ArrayBuffer[],
  ];
  expect(message.sourceUrl).toBe("https://example.test/portrait.jpg");
  expect(message.pixelsBuffer).toBe(pixels.buffer);
  expect(transfer).toEqual([pixels.buffer]);
});

test("closes both bitmaps from unknown responses", () => {
  const worker = new FakeWorker();
  const client = new RotoscopeLabClient(() => worker as unknown as Worker);
  const outputClose = jest.fn();
  const diagnosticClose = jest.fn();
  worker.emit(
    success(
      404,
      { close: outputClose } as unknown as ImageBitmap,
      { close: diagnosticClose } as unknown as ImageBitmap,
    ),
  );
  expect(outputClose).toHaveBeenCalledTimes(1);
  expect(diagnosticClose).toHaveBeenCalledTimes(1);
  client.dispose();
});

test("crash and dispose settle work and terminate the lab worker", async () => {
  const crashedWorker = new FakeWorker();
  const crashed = new RotoscopeLabClient(
    () => crashedWorker as unknown as Worker,
  );
  const rejected = expect(crashed.render(request())).rejects.toThrow(
    "rotoscope lab worker crashed",
  );
  crashedWorker.crash();
  await rejected;
  expect(crashed.available()).toBe(false);

  const disposedWorker = new FakeWorker();
  const disposed = new RotoscopeLabClient(
    () => disposedWorker as unknown as Worker,
  );
  const pending = disposed.render(request());
  disposed.dispose();
  await expect(pending).resolves.toBeNull();
  expect(disposedWorker.terminate).toHaveBeenCalledTimes(1);
  disposed.dispose();
  expect(disposedWorker.terminate).toHaveBeenCalledTimes(1);
});
