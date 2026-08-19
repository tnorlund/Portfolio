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

const successResult = (): RotoscopeRenderSuccess => {
  const pixels = new Uint8ClampedArray([10, 20, 30, 255, 40, 50, 60, 255]);
  return {
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
};

const canvasContext = () => ({
  clearRect: jest.fn(),
  createImageData: jest.fn((width: number, height: number) => ({
    data: new Uint8ClampedArray(width * height * 4),
    width,
    height,
  })),
  putImageData: jest.fn(),
});

beforeEach(() => {
  mockAvailable.mockReturnValue(true);
  mockRender.mockReset();
  mockDispose.mockReset();
});

test("hides the basin map, source portrait, and caption until a worker is unavailable", () => {
  render(<RotoscopePortrait />);
  expect(
    screen.queryByRole("img", {
      name: "Catchment basins outlining Tyler Norlund's portrait",
    }),
  ).not.toBeInTheDocument();
  const hiddenSource = document.querySelector<HTMLImageElement>(
    'img[aria-hidden="true"]',
  );
  expect(hiddenSource).not.toBeNull();
  expect(hiddenSource).toHaveAttribute("src", "/rotoscope-portrait.jpg");
  expect(hiddenSource).toHaveAttribute("width", "960");
  expect(hiddenSource).toHaveAttribute("height", "720");
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Replay" })).not.toBeInTheDocument();
  expect(screen.queryByText("Best-features rotoscope.")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Paper" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Source" })).not.toBeInTheDocument();
});

test("keeps the basin map when workers are unavailable", async () => {
  mockAvailable.mockReturnValue(false);
  render(<RotoscopePortrait />);
  const hiddenSource = document.querySelector<HTMLImageElement>(
    'img[aria-hidden="true"]',
  );
  expect(hiddenSource).not.toBeNull();
  fireEvent.load(hiddenSource as HTMLImageElement);
  await waitFor(() =>
    expect(
      screen.getByRole("img", {
        name: "Catchment basins outlining Tyler Norlund's portrait",
      }),
    ).toHaveAttribute("src", "/rotoscope-basins.webp"),
  );
  expect(screen.queryByText("From my 2017 paper.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Replay" })).not.toBeInTheDocument();
});

test("reduced motion paints the complete result without scheduling a reveal", async () => {
  jest.useFakeTimers();
  mockRender.mockResolvedValue(successResult());
  const putImageData = jest.fn();
  const context = {
    ...canvasContext(),
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
  expect(Array.from(painted.data)).toEqual([10, 20, 30, 255, 40, 50, 60, 255]);
  expect(
    screen.queryByRole("img", {
      name: "Catchment basins outlining Tyler Norlund's portrait",
    }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Best-features rotoscope.")).not.toBeInTheDocument();
  requestAnimationFrame.mockRestore();
  jest.useRealTimers();
});

test("Replay keeps the basin map hidden and requests the dense homepage pass", async () => {
  jest.useFakeTimers();
  mockRender.mockResolvedValue(successResult());
  (HTMLCanvasElement.prototype.getContext as jest.Mock).mockReturnValue(canvasContext());
  (window.matchMedia as jest.Mock).mockReturnValue({ matches: false });
  const raf = jest.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);

  const { container } = render(<RotoscopePortrait />);
  fireEvent.load(container.querySelector('img[aria-hidden="true"]') as HTMLImageElement);
  await act(async () => {
    jest.advanceTimersByTime(90);
    await Promise.resolve();
  });

  const replay = await screen.findByRole("button", { name: "Replay" });
  expect(replay.tagName).toBe("FIGURE");
  expect(mockRender).toHaveBeenCalledWith(
    expect.objectContaining({
      width: 960,
      height: 720,
      options: expect.objectContaining({
        blurRadius: 6,
        markerBudget: 1600,
        quotas: { face: 0.7, body: 0.22, background: 0.08 },
        spacing: { face: 1, body: 4, background: 8 },
      }),
    }),
  );
  expect(container.querySelector('[data-reveal-state="revealing"]')).not.toBeNull();
  expect(
    screen.queryByRole("img", {
      name: "Catchment basins outlining Tyler Norlund's portrait",
    }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Best-features rotoscope.")).not.toBeInTheDocument();

  fireEvent.click(replay);
  await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(2));
  expect(
    screen.queryByRole("img", {
      name: "Catchment basins outlining Tyler Norlund's portrait",
    }),
  ).not.toBeInTheDocument();

  fireEvent.keyDown(replay, { key: "Enter" });
  await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(3));
  fireEvent.keyDown(replay, { key: " " });
  await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(4));
  raf.mockRestore();
  jest.useRealTimers();
});
