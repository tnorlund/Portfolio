import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import fs from "fs";
import path from "path";
import SynthesisPipeline, { advanceAutoplay } from ".";
import { ACT_COUNT, ACTS } from "./pipelineData";
import { LABEL_COLORS } from "../labelStyles";

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
  // Stub rAF so the autoplay clock never advances on its own during tests —
  // the timeline math is verified separately via advanceAutoplay(). Component
  // tests then assert deterministic state (opening act, manual navigation).
  jest
    .spyOn(window, "requestAnimationFrame")
    .mockImplementation(() => 0 as unknown as number);
  jest.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
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

describe("advanceAutoplay (pure timeline math)", () => {
  test("advancing within a dwell increments progress, same act", () => {
    const s = advanceAutoplay(2, 0.2, 1000, 5000, ACT_COUNT);
    expect(s.activeAct).toBe(2);
    expect(s.actProgress).toBeCloseTo(0.4, 5);
  });

  test("crossing the dwell rolls into the next act with the remainder", () => {
    // 0.8 + 4000/5000 = 1.6 -> next act at 0.6
    const s = advanceAutoplay(2, 0.8, 4000, 5000, ACT_COUNT);
    expect(s.activeAct).toBe(3);
    expect(s.actProgress).toBeCloseTo(0.6, 5);
  });

  test("the finale act wraps back to the first", () => {
    const s = advanceAutoplay(ACT_COUNT - 1, 0.9, 1000, 5000, ACT_COUNT);
    expect(s.activeAct).toBe(0);
    expect(s.actProgress).toBeCloseTo(0.1, 5);
  });

  test("a non-positive dwell resolves the act immediately", () => {
    const s = advanceAutoplay(1, 0.3, 16, 0, ACT_COUNT);
    expect(s).toEqual({ activeAct: 1, actProgress: 1 });
  });
});

describe("SynthesisPipeline (autoplay mode)", () => {
  test("renders the in-place autoplay stage with no merchant toggle", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    const figure = screen.getByTestId("synthesis-pipeline");
    expect(figure).toHaveAttribute("data-mode", "autoplay");
    // No scroll-through track: the sticky scroller is gone.
    expect(figure.querySelector('[style*="vh"]')).toBeNull();
    // The merchant toggle was removed — the figure is single-merchant now.
    expect(screen.queryByTestId("merchant-sprouts")).not.toBeInTheDocument();
    expect(screen.queryByTestId("merchant-costco")).not.toBeInTheDocument();
    // Autoplay opens on the raw-material fan.
    expect(screen.getByTestId("act-raw")).toBeInTheDocument();
  });

  test("chrome is gone: no visible caption/eyebrow, act label is sr-only", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    // The card/caption/eyebrow chrome was removed — the content is the figure.
    expect(screen.queryByTestId("act-caption")).not.toBeInTheDocument();
    expect(screen.queryByTestId("act-eyebrow")).not.toBeInTheDocument();
    // The act label survives only for screen readers + the e2e gate.
    const label = screen.getByTestId("act-headline");
    expect(label).toHaveAttribute("aria-live", "polite");
    expect(label).toHaveTextContent(ACTS[0].headline);
  });

  test("there are seven act dots, one per act", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    expect(ACT_COUNT).toBe(7);
    ACTS.forEach((meta) => {
      expect(screen.getByTestId(`act-dot-${meta.index}`)).toBeInTheDocument();
    });
  });

  test("act dots navigate to the finale and pause autoplay", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    const figure = screen.getByTestId("synthesis-pipeline");
    expect(figure).not.toHaveAttribute("data-paused");

    // Jump to the final (finale) act via its dot.
    fireEvent.click(screen.getByTestId(`act-dot-${ACT_COUNT - 1}`));
    await flushAssets();

    const finaleMeta = ACTS[ACT_COUNT - 1];
    expect(finaleMeta.id).toBe("finale");
    expect(screen.getByTestId("act-headline")).toHaveTextContent(
      finaleMeta.headline,
    );
    expect(screen.getByTestId("act-finale")).toBeInTheDocument();
    // Manual navigation pauses autoplay.
    expect(figure).toHaveAttribute("data-paused");
  });
});

describe("SynthesisPipeline finale act", () => {
  test("renders one receipt card per merchant (Sprouts, Costco, Vons)", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    fireEvent.click(screen.getByTestId(`act-dot-${ACT_COUNT - 1}`));
    await flushAssets();

    const cards = screen.getAllByTestId("finale-card");
    expect(cards).toHaveLength(3);
    expect(cards.map((c) => c.getAttribute("data-merchant"))).toEqual([
      "sprouts",
      "costco",
      "vons",
    ]);
    ["Sprouts", "Costco", "Vons"].forEach((name) =>
      expect(screen.getByText(name)).toBeInTheDocument(),
    );
    // Chrome (headline/caption) is intentionally gone in autoplay mode — the
    // three named cards carry the generalization beat on their own.
  });

  test("each card pairs the real scan with the synth render for proof", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    fireEvent.click(screen.getByTestId(`act-dot-${ACT_COUNT - 1}`));
    await flushAssets();

    // Every card overlays the real scan on the synthesized render.
    expect(screen.getAllByTestId("finale-image")).toHaveLength(3);
    expect(screen.getAllByTestId("finale-real")).toHaveLength(3);
    const sprouts = screen.getByRole("img", {
      name: /synthetic sprouts receipt/i,
    });
    const sproutsReal = screen.getByRole("img", {
      name: /real sprouts receipt scan/i,
    });
    expect(sprouts.getAttribute("src")).toMatch(/sprouts\/final\.webp$/);
    expect(sproutsReal.getAttribute("src")).toMatch(/sprouts\/real\.webp$/);
  });

  test("cards render at their true (different) per-merchant proportions", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    fireEvent.click(screen.getByTestId(`act-dot-${ACT_COUNT - 1}`));
    await flushAssets();

    // The frame's aspect ratio is the receipt's real 760xH — Costco (tallest)
    // and Sprouts (shortest) must differ, which is the whole point.
    const frameFor = (merchant: string) =>
      screen
        .getAllByTestId("finale-card")
        .find((c) => c.getAttribute("data-merchant") === merchant)!
        .querySelector<HTMLElement>('[style*="aspect-ratio"]')!;
    const sprouts = frameFor("sprouts").style.aspectRatio;
    const costco = frameFor("costco").style.aspectRatio;
    const vons = frameFor("vons").style.aspectRatio;
    expect(sprouts).toBe("760 / 2471");
    expect(costco).toBe("760 / 2999");
    expect(vons).toBe("760 / 2732");
    // Distinct proportions -> visibly different heights at a common width.
    expect(new Set([sprouts, costco, vons]).size).toBe(3);
  });

  test("a receipt image that fails to load degrades to a named fallback", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    fireEvent.click(screen.getByTestId(`act-dot-${ACT_COUNT - 1}`));
    await flushAssets();

    const vonsCard = screen
      .getAllByTestId("finale-card")
      .find((c) => c.getAttribute("data-merchant") === "vons")!;
    const img = vonsCard.querySelector<HTMLImageElement>(
      '[data-testid="finale-image"]',
    )!;
    expect(img).toBeInTheDocument();

    // Simulate the asset 404'ing (a merchant print may not be generated yet).
    fireEvent.error(img);

    expect(
      vonsCard.querySelector('[data-testid="finale-fallback"]'),
    ).toBeInTheDocument();
    expect(vonsCard.querySelector('[data-testid="finale-image"]')).toBeNull();
    // The merchant name still labels the card.
    expect(screen.getByText("Vons")).toBeInTheDocument();
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
    // All seven acts are present as static sections, including the merged
    // assemble act and the finale.
    expect(screen.getByTestId("static-act-raw")).toBeInTheDocument();
    expect(screen.getByTestId("static-act-assemble")).toBeInTheDocument();
    expect(screen.getByTestId("static-act-finale")).toBeInTheDocument();
    expect(screen.getByTestId("act-penpath")).toBeInTheDocument();
    // The finale fans out to three merchant cards.
    expect(screen.getAllByTestId("finale-card")).toHaveLength(3);
  });

  test("the font atlas marks exactly one hero cell (the FLIP target)", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    const heroCells = screen
      .getAllByTestId("font-cell")
      .filter((cell) => cell.getAttribute("data-hero") === "true");
    // One glyph is the hero that flew in from the thermal act into its slot.
    expect(heroCells).toHaveLength(1);
  });

  test("atlas glyphs use the alpha-mask technique (mask-image points at the glyph png)", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    // The glyph div paints currentColor through an alpha mask of the glyph png.
    // (jsdom drops -webkit- props from the serialized style; the -webkit-mask
    // + rendered currentColor are asserted in the Playwright gate instead.)
    const glyph = screen
      .getAllByTestId("font-cell")[0]
      .firstElementChild as HTMLElement;
    const style = glyph.getAttribute("style") || "";
    expect(style).toMatch(/mask-image:\s*url\([^)]*font_grid[^)]*\.png/);
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

  test("the assemble act types the receipt then draws LayoutLM boxes", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    // The merged act renders the receipt-assembly canvas (parallel typing).
    expect(screen.getByTestId("assemble-canvas")).toBeInTheDocument();
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

  test("assemble label boxes use the LayoutLM LABEL_COLORS + stroke styling", async () => {
    render(<SynthesisPipeline />);
    await flushAssets();

    await waitFor(() =>
      expect(screen.getAllByTestId("final-label-box").length).toBeGreaterThan(
        0,
      ),
    );
    const box = screen.getAllByTestId("final-label-box")[0];
    const family = box.getAttribute("data-family")!;
    // Mirror the LayoutLM inference viz exactly: LABEL_COLORS fill/stroke,
    // fillOpacity 0.3, strokeWidth 2, no vectorEffect.
    const expected = LABEL_COLORS[family] || LABEL_COLORS.O;
    expect(box.getAttribute("fill")).toBe(expected);
    expect(box.getAttribute("stroke")).toBe(expected);
    expect(box.getAttribute("fill-opacity")).toBe("0.3");
    expect(box.getAttribute("stroke-width")).toBe("2");
    expect(box.getAttribute("vector-effect")).toBeNull();
  });
});
