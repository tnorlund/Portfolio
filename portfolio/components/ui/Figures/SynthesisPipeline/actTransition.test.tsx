import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import fs from "fs";
import path from "path";
import SynthesisPipeline from ".";
import {
  ACT_TRANSITION_MS,
  initialTransitionState,
  transitionReducer,
  useActTransition,
} from "./actTransition";

/* ---- Pure reducer: enter / leave / settle / reduced-motion ------------- */

describe("transitionReducer (pure state machine)", () => {
  test("targeting a new act moves the old one to `leaving`", () => {
    const s = transitionReducer(initialTransitionState(0), {
      type: "target",
      act: 3,
      animate: true,
    });
    expect(s).toEqual({ current: 3, leaving: 0 });
  });

  test("settle clears the leaving layer once the window elapses", () => {
    const mid = { current: 3, leaving: 0 };
    expect(transitionReducer(mid, { type: "settle" })).toEqual({
      current: 3,
      leaving: null,
    });
  });

  test("targeting the act already shown is a no-op (same reference)", () => {
    const state = { current: 3, leaving: null };
    expect(
      transitionReducer(state, { type: "target", act: 3, animate: true }),
    ).toBe(state);
  });

  test("reduced motion (animate=false) swaps instantly with no leaving layer", () => {
    const s = transitionReducer(initialTransitionState(1), {
      type: "target",
      act: 5,
      animate: false,
    });
    expect(s).toEqual({ current: 5, leaving: null });
  });

  test("retargeting mid-window drops the stale leaving act, keeps the latest pair", () => {
    const inFlight = { current: 3, leaving: 0 }; // 0 -> 3 crossfade running
    const s = transitionReducer(inFlight, { type: "target", act: 6, animate: true });
    expect(s).toEqual({ current: 6, leaving: 3 });
  });

  test("settle on a settled state is a no-op (same reference)", () => {
    const settled = { current: 2, leaving: null };
    expect(transitionReducer(settled, { type: "settle" })).toBe(settled);
  });
});

/* ---- Hook: timers drive the leave -> cleanup lifecycle ----------------- */

describe("useActTransition (timer-driven lifecycle)", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("changing the target opens a crossfade, then cleans it up after the window", () => {
    const { result, rerender } = renderHook(
      ({ act: a }) => useActTransition(a, true, ACT_TRANSITION_MS),
      { initialProps: { act: 0 } },
    );
    expect(result.current).toEqual({ current: 0, leaving: null });

    rerender({ act: 2 });
    expect(result.current).toEqual({ current: 2, leaving: 0 });

    act(() => {
      jest.advanceTimersByTime(ACT_TRANSITION_MS);
    });
    expect(result.current).toEqual({ current: 2, leaving: null });
  });

  test("reduced motion (animate=false) never mounts a leaving layer", () => {
    const { result, rerender } = renderHook(
      ({ act: a }) => useActTransition(a, false, ACT_TRANSITION_MS),
      { initialProps: { act: 0 } },
    );
    rerender({ act: 4 });
    expect(result.current).toEqual({ current: 4, leaving: null });
  });
});

/* ---- Component: both acts present mid-transition, one after ------------ */

const PIPELINE_DIR = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/pipeline",
);

const mockFetch = () =>
  jest.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const rel = url.startsWith("/synthetic-receipts/pipeline/")
      ? url.replace("/synthetic-receipts/pipeline/", "")
      : "";
    const full = path.join(PIPELINE_DIR, rel);
    if (rel && fs.existsSync(full)) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(JSON.parse(fs.readFileSync(full, "utf-8"))),
      } as Response);
    }
    return Promise.resolve({ ok: false } as Response);
  }) as jest.Mock;

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({ ref: jest.fn(), inView: true }),
}));

describe("SynthesisPipeline crossfade (both acts mounted mid-transition)", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    global.fetch = mockFetch();
    jest
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation(() => 0 as unknown as number);
    jest.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        addEventListener: jest.fn(),
        removeEventListener: jest.fn(),
        addListener: jest.fn(),
        removeListener: jest.fn(),
        onchange: null,
        dispatchEvent: jest.fn(),
      }),
    });
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  const flushMicrotasks = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  test("jumping acts mounts both layers during the window, one after it settles", async () => {
    render(<SynthesisPipeline />);
    await flushMicrotasks();

    // Opening state: only the raw-material layer is mounted.
    expect(screen.getByTestId("act-layer-raw")).toBeInTheDocument();
    expect(screen.queryByTestId("act-layer-labels")).not.toBeInTheDocument();

    // Jump to the final act: the outgoing (raw) and incoming (labels) layers
    // overlap during the crossfade window.
    act(() => {
      fireEvent.click(screen.getByTestId("act-dot-labels"));
    });
    expect(screen.getByTestId("act-layer-raw")).toBeInTheDocument();
    expect(screen.getByTestId("act-layer-labels")).toBeInTheDocument();
    expect(screen.getByTestId("act-layer-raw")).toHaveAttribute(
      "data-phase",
      "leaving",
    );
    expect(screen.getByTestId("act-layer-labels")).toHaveAttribute(
      "data-phase",
      "entering",
    );

    // After the window elapses only the incoming layer remains.
    act(() => {
      jest.advanceTimersByTime(ACT_TRANSITION_MS);
    });
    expect(screen.queryByTestId("act-layer-raw")).not.toBeInTheDocument();
    expect(screen.getByTestId("act-layer-labels")).toBeInTheDocument();
    expect(screen.getByTestId("act-layer-labels")).toHaveAttribute(
      "data-phase",
      "active",
    );
  });
});
