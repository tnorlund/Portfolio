import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  ROTOSCOPE_WORKER_VERSION,
  type RotoscopeRenderSuccess,
} from "./workerProtocol";

const mockAvailable = jest.fn(() => true);
const mockRender = jest.fn();
const mockDispose = jest.fn();

jest.mock("./workerClient", () => ({
  RotoscopeWorkerClient: jest.fn().mockImplementation(() => ({
    available: mockAvailable,
    render: mockRender,
    dispose: mockDispose,
  })),
}));

import RotoscopePortrait from "./RotoscopePortrait";

beforeEach(() => {
  mockAvailable.mockReturnValue(true);
  mockRender.mockReset();
  mockDispose.mockReset();
});

test("keeps the catchment basin map as the immediate accessible image", () => {
  render(<RotoscopePortrait />);
  const image = screen.getByRole("img", {
    name: "Catchment basins outlining Tyler Norlund's portrait",
  });
  expect(image).toHaveAttribute("src", "/rotoscope-basins.webp");
  expect(image).toHaveAttribute("loading", "eager");
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Replay" })).not.toBeInTheDocument();
  expect(screen.getByText("Best-features rotoscope.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Paper" })).toHaveAttribute(
    "href",
    "https://doi.org/10.1109/ACSSC.2017.8335175",
  );
});

test("keeps the basin map when workers are unavailable", () => {
  mockAvailable.mockReturnValue(false);
  render(<RotoscopePortrait />);
  const hiddenSource = document.querySelector<HTMLImageElement>(
    'img[aria-hidden="true"]',
  );
  expect(hiddenSource).not.toBeNull();
  fireEvent.load(hiddenSource as HTMLImageElement);
  expect(
    screen.getByText("From my 2017 paper."),
  ).toBeInTheDocument();
});

test("reduced motion paints the complete result without scheduling a reveal", async () => {
  jest.useFakeTimers();
  const pixels = new Uint8ClampedArray([
    10, 20, 30, 255,
    40, 50, 60, 255,
  ]);
  const result: RotoscopeRenderSuccess = {
    version: ROTOSCOPE_WORKER_VERSION,
    type: "result",
    id: 1,
    width: 2,
    height: 1,
    markerCount: 2,
    tierCounts: { face: 1, body: 1, background: 0 },
    path: "wasm-scalar",
    timings: {
      decodeAndResizeMs: 1,
      wasmLoadMs: 0,
      focusMapMs: 0,
      pipelineMs: 2,
      paintMs: 1,
      totalMs: 4,
    },
    pixelsBuffer: pixels.buffer,
    revealPhasesBuffer: Uint8Array.from([0, 35]).buffer,
    revealPhaseCount: 36,
    revealBasinCount: 2,
  };
  mockRender.mockResolvedValue(result);
  const putImageData = jest.fn();
  const context = {
    clearRect: jest.fn(),
    createImageData: jest.fn((width: number, height: number) => ({
      data: new Uint8ClampedArray(width * height * 4),
      width,
      height,
    })),
    putImageData,
  };
  (HTMLCanvasElement.prototype.getContext as jest.Mock).mockReturnValue(context);
  const requestAnimationFrame = jest.spyOn(window, "requestAnimationFrame");
  (window.matchMedia as jest.Mock).mockReturnValue({ matches: true });

  const { container } = render(<RotoscopePortrait />);
  fireEvent.load(container.querySelector('img[aria-hidden="true"]') as HTMLImageElement);
  await act(async () => {
    jest.advanceTimersByTime(90);
    await Promise.resolve();
  });

  await waitFor(() =>
    expect(container.querySelector('[data-reveal-state="complete"]')).not.toBeNull(),
  );
  expect(requestAnimationFrame).not.toHaveBeenCalled();
  expect(putImageData).toHaveBeenCalledTimes(1);
  const painted = putImageData.mock.calls[0][0] as { data: Uint8ClampedArray };
  expect(Array.from(painted.data)).toEqual(Array.from(pixels));
  requestAnimationFrame.mockRestore();
  jest.useRealTimers();
});
