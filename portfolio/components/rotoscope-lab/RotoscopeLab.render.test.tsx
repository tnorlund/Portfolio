import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { normalizeRotoscopeOptions } from "../home/Rotoscope/algorithm";
import { normalizeMarkerExperiment } from "./labAlgorithm";
import type { RotoscopeLabRenderSuccess } from "./protocol";

const mockRender = jest.fn();
const mockAvailable = jest.fn(() => true);
const mockDispose = jest.fn();

jest.mock("./client", () => ({
  RotoscopeLabClient: jest.fn().mockImplementation(() => ({
    available: mockAvailable,
    render: mockRender,
    dispose: mockDispose,
  })),
}));

import RotoscopeLab from "./RotoscopeLab";

const bitmap = (): ImageBitmap =>
  ({ close: jest.fn() }) as unknown as ImageBitmap;

const result = (
  markerDigest: string,
  outputBitmap = bitmap(),
  diagnosticBitmap = bitmap(),
  visionAvailable = true,
): RotoscopeLabRenderSuccess => ({
  version: 3,
  type: "result",
  id: 1,
  width: 4,
  height: 3,
  markerCount: 3,
  tierCounts: { face: 2, body: 1, background: 0 },
  markerDigest,
  labelDigest: "90abcdef",
  vision: {
    available: visionAvailable,
    featureCount: visionAvailable ? 76 : 0,
    markerCount: visionAvailable ? 61 : 0,
    faceLandmarkCount: visionAvailable ? 76 : 0,
    captureQuality: visionAvailable ? 0.659 : null,
    ...(visionAvailable ? {} : { message: "fixture unavailable" }),
  },
  visionFeatures: visionAvailable
    ? [
        {
          id: "face.leftEye.0",
          label: "Left Eye",
          kind: "face-landmark",
          group: "leftEye",
          point: { x: 0.4, y: 0.4 },
          confidence: 0.9,
        },
      ]
    : [],
  path: "scalar-lab",
  normalizedExperiment: normalizeMarkerExperiment(),
  normalizedBaseOptions: normalizeRotoscopeOptions({ markerBudget: 3 }, 12),
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
  markerIndicesBuffer: Uint32Array.from([0, 5, 11]).buffer,
});

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
};

describe("rendered lab results", () => {
  const transferFromImageBitmap = jest.fn();
  const twoDimensionalContext = {
    clearRect: jest.fn(),
    beginPath: jest.fn(),
    arc: jest.fn(),
    fill: jest.fn(),
    stroke: jest.fn(),
    moveTo: jest.fn(),
    lineTo: jest.fn(),
    strokeText: jest.fn(),
    fillText: jest.fn(),
  };

  beforeEach(() => {
    jest.useFakeTimers();
    mockRender.mockReset();
    mockAvailable.mockReturnValue(true);
    mockDispose.mockReset();
    transferFromImageBitmap.mockReset();
    (HTMLCanvasElement.prototype.getContext as jest.Mock).mockImplementation(
      (kind: string) =>
        kind === "bitmaprenderer"
          ? { transferFromImageBitmap }
          : twoDimensionalContext,
    );
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("paints an accepted result and updates the rendered metrics", async () => {
    const accepted = result("12345678");
    mockRender.mockResolvedValue(accepted);
    const { container } = render(<RotoscopeLab />);
    fireEvent.load(container.querySelector("img") as HTMLImageElement);

    await act(async () => {
      jest.advanceTimersByTime(130);
      await Promise.resolve();
    });

    await waitFor(() => expect(screen.getByText("Rendered")).toBeInTheDocument());
    expect(transferFromImageBitmap).toHaveBeenCalledTimes(2);
    const basinMetric = screen
      .getAllByText("Basins")
      .find((element) => element.tagName === "DT");
    const faceMetric = screen
      .getAllByText("Face")
      .find((element) => element.tagName === "DT");
    expect(within(basinMetric?.parentElement as HTMLElement).getByText("3")).toBeInTheDocument();
    expect(within(faceMetric?.parentElement as HTMLElement).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("markers 12345678")).toBeInTheDocument();
    expect(screen.getByText("76")).toBeInTheDocument();
    expect(screen.getByText("61")).toBeInTheDocument();
    expect(twoDimensionalContext.fillText).toHaveBeenCalledWith(
      "Left Eye",
      expect.any(Number),
      expect.any(Number),
    );

    fireEvent.click(screen.getByRole("button", { name: "Basin map" }));
    fireEvent.click(
      within(screen.getByRole("group", { name: "Strategy" })).getByRole(
        "button",
        { name: "Best features" },
      ),
    );
    expect(
      screen.getByRole("img", { name: "Catchment basin diagnostic map" }),
    ).toBeInTheDocument();
    expect(container.querySelector("canvas[data-crosshair]"))?.toHaveAttribute(
      "data-crosshair",
      "hidden",
    );
  });

  test("shows a nonfatal Gaussian fallback when Vision artifacts are unavailable", async () => {
    mockRender.mockResolvedValue(result("fallback", bitmap(), bitmap(), false));
    const { container } = render(<RotoscopeLab />);
    fireEvent.load(container.querySelector("img") as HTMLImageElement);

    await act(async () => {
      jest.advanceTimersByTime(130);
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(
        screen.getByText("Vision unavailable — Gaussian fallback rendered"),
      ).toBeInTheDocument(),
    );
  });

  test("closes a stale result and paints only the latest settings", async () => {
    const first = deferred<RotoscopeLabRenderSuccess | null>();
    const stale = result("stale000");
    const latest = result("latest00");
    mockRender
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValueOnce(latest);
    const { container } = render(<RotoscopeLab />);
    fireEvent.load(container.querySelector("img") as HTMLImageElement);

    await act(async () => {
      jest.advanceTimersByTime(130);
    });
    expect(mockRender).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByRole("slider", { name: "Basins" }), {
      target: { value: "400" },
    });
    await act(async () => {
      first.resolve(stale);
      await Promise.resolve();
    });
    expect(stale.outputBitmap?.close).toHaveBeenCalledTimes(1);
    expect(stale.diagnosticBitmap?.close).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("markers stale000")).not.toBeInTheDocument();

    await act(async () => {
      jest.advanceTimersByTime(130);
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByText("markers latest00")).toBeInTheDocument());
    expect(mockRender).toHaveBeenCalledTimes(2);
  });

  test("manual render cancels the identical debounced render", async () => {
    mockRender.mockResolvedValue(result("manual00"));
    const { container } = render(<RotoscopeLab />);
    fireEvent.load(container.querySelector("img") as HTMLImageElement);
    fireEvent.change(screen.getByRole("slider", { name: "Basins" }), {
      target: { value: "400" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Render now" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockRender).toHaveBeenCalledTimes(1);

    await act(async () => {
      jest.advanceTimersByTime(130);
      await Promise.resolve();
    });
    expect(mockRender).toHaveBeenCalledTimes(1);
  });
});
