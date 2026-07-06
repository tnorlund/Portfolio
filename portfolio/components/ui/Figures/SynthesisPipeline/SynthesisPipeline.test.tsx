import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import fs from "fs";
import path from "path";
import SynthesisPipeline, { scrollStateFor } from ".";
import { ACT_COUNT } from "./pipelineData";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({ ref: jest.fn(), inView: true }),
}));

const PIPELINE_DIR = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/pipeline",
);

// Serve real committed JSON (skeleton, dot_params) through fetch; JSON assets
// that have not been generated yet (style/compose/final) resolve to !ok so the
// missing-asset fallbacks are exercised exactly as production would hit them.
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

let matchMediaReduced = false;

beforeEach(() => {
  global.fetch = mockFetch();
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: query.includes("reduce") ? matchMediaReduced : false,
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
  jest.restoreAllMocks();
  matchMediaReduced = false;
});

const flushAssets = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("scrollStateFor (pure mapping)", () => {
  test("no scrollable distance pins to the first act", () => {
    expect(scrollStateFor({ top: 0, height: 500 }, 900, ACT_COUNT)).toEqual({
      activeAct: 0,
      actProgress: 0,
    });
  });

  test("top of the scroller is act 0 at progress 0", () => {
    const distance = ACT_COUNT * 1000 - 1000;
    const s = scrollStateFor(
      { top: 0, height: ACT_COUNT * 1000 },
      1000,
      ACT_COUNT,
    );
    expect(s.activeAct).toBe(0);
    expect(s.actProgress).toBeCloseTo(0, 5);
    expect(distance).toBeGreaterThan(0);
  });

  test("fully scrolled clamps to the last act", () => {
    const height = ACT_COUNT * 1000;
    const distance = height - 1000;
    const s = scrollStateFor({ top: -distance, height }, 1000, ACT_COUNT);
    expect(s.activeAct).toBe(ACT_COUNT - 1);
    expect(s.actProgress).toBeCloseTo(1, 5);
  });

  test("halfway through the scroll lands mid-timeline", () => {
    const height = ACT_COUNT * 1000;
    const distance = height - 1000;
    const s = scrollStateFor({ top: -distance / 2, height }, 1000, ACT_COUNT);
    expect(s.activeAct).toBe(Math.floor(ACT_COUNT / 2));
  });
});

describe("SynthesisPipeline (scroll mode)", () => {
  test("renders the scroll-driven stage with both merchant toggles", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    const figure = screen.getByTestId("synthesis-pipeline");
    expect(figure).toHaveAttribute("data-mode", "scroll");
    expect(screen.getByTestId("merchant-sprouts")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("merchant-costco")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    // First act on load is the raw-material fan.
    expect(screen.getByTestId("act-raw")).toBeInTheDocument();
  });

  test("merchant toggle switches asset root and persists selection", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    const costcoScan = screen.getAllByAltText(/sprouts receipt scan/i);
    expect(costcoScan.length).toBeGreaterThan(0);

    fireEvent.click(screen.getByTestId("merchant-costco"));
    await flushAssets();

    expect(screen.getByTestId("merchant-costco")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // Act-1 thumbnails now point at the costco root.
    await waitFor(() =>
      expect(
        screen.getAllByAltText(/costco receipt scan/i).length,
      ).toBeGreaterThan(0),
    );
    // The costco skeleton JSON is fetched (was not loaded initially).
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("costco/char_skeleton.json"),
    );
  });
});

describe("SynthesisPipeline (reduced motion)", () => {
  beforeEach(() => {
    matchMediaReduced = true;
  });

  test("renders a static stack of every act, fully resolved", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    expect(screen.getByTestId("synthesis-pipeline")).toHaveAttribute(
      "data-mode",
      "static",
    );
    // All eight acts are present as static sections.
    expect(screen.getByTestId("static-act-raw")).toBeInTheDocument();
    expect(screen.getByTestId("static-act-labels")).toBeInTheDocument();
    expect(screen.getByTestId("act-penpath")).toBeInTheDocument();
  });

  test("pen-path act draws SVG paths + anchor dots from the real skeleton", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    await waitFor(() =>
      expect(screen.getAllByTestId("pen-path").length).toBeGreaterThan(0),
    );
    // The committed Sprouts skeleton has six nodes.
    expect(screen.getAllByTestId("anchor-dot").length).toBe(6);
    expect(screen.getByText("6 nodes")).toBeInTheDocument();
  });

  test("weight slider is present and re-stamps on change", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    const slider = await screen.findByTestId("weight-slider");
    expect(slider).toHaveAttribute("aria-label", "Dot weight");
    fireEvent.change(slider, { target: { value: "1.33" } });
    expect(screen.getByText("1.33")).toBeInTheDocument();
    // Reaching the bold weight surfaces the merchant's measured-weight callout.
    expect(screen.getByText(/measured BALANCE DUE weight/i)).toBeInTheDocument();
  });

  test("renders the composed content + measured style + final labels for sprouts", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    // Measured style: the committed display strings appear verbatim.
    expect(
      screen.getByText(/Underlined ~41% of the time/i),
    ).toBeInTheDocument();
    // Compose: real token groups reveal (not the pending note).
    expect(screen.getAllByTestId("compose-group").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("asset-pending")).not.toBeInTheDocument();
    // Final labels resolve -> ground-truth boxes + the counter.
    await waitFor(() =>
      expect(screen.getAllByTestId("final-label-box").length).toBeGreaterThan(
        10,
      ),
    );
    expect(screen.getByTestId("labels-counter")).toHaveTextContent(
      /zero manual labels/i,
    );
  });

  test("a merchant without composed/final assets falls back gracefully", async () => {
    // Simulate the pre-generation state explicitly (all merchants now have
    // real assets on disk, but the fallback path must keep working for the
    // next merchant that doesn't yet).
    const base = mockFetch();
    global.fetch = jest.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (
        url.includes("/costco/compose_steps.json") ||
        url.includes("/costco/final.labels.json")
      ) {
        return Promise.resolve({ ok: false } as Response);
      }
      return base(input);
    }) as jest.Mock;
    render(<SynthesisPipeline />);
    await flushAssets();

    fireEvent.click(screen.getByTestId("merchant-costco"));
    await flushAssets();

    await waitFor(() =>
      expect(
        screen.getByText(/compose_steps\.json \+ final\.labels\.json/i),
      ).toBeInTheDocument(),
    );
    // No final labels -> the "zero manual labels" counter must not assert.
    expect(screen.queryByTestId("labels-counter")).not.toBeInTheDocument();
    // Costco's own measured style still renders.
    expect(
      screen.getByText(/white-on-black/i),
    ).toBeInTheDocument();
  });

  test("costco with full assets renders compose groups and the labels counter", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    fireEvent.click(screen.getByTestId("merchant-costco"));
    await flushAssets();

    await waitFor(() =>
      expect(screen.getByTestId("labels-counter")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/compose_steps\.json \+ final\.labels\.json/i),
    ).not.toBeInTheDocument();
  });
});
