import { LineItemDemoReceipt } from "../../../../types/api";
import {
  HOLD_DURATION,
  TRANSITION_DURATION,
  buildStagePlan,
  computeFrame,
  displayItems,
  planDuration,
  revealCount,
  stageDone,
  stageStarted,
} from "./decoderTimeline";

const makeReceipt = (
  overrides: Partial<LineItemDemoReceipt> = {}
): LineItemDemoReceipt => ({
  image_id: "test",
  receipt_id: 1,
  merchant: "Test Mart",
  image: { width: 800, height: 2400, cdn_s3_key: "assets/test/1.jpg" },
  words: [],
  items_line_ids: [1, 2],
  bands: [
    {
      band_id: 0,
      line_ids: [1],
      word_refs: [[1, 1]],
      text: "MILK $3.99",
      y: 0.8,
      amounts: [3.99],
      role: "PRICE",
      guard: null,
      prior: null,
      outcome: "item",
    },
    {
      band_id: 1,
      line_ids: [2],
      word_refs: [[2, 1]],
      text: "EGGS $5.99",
      y: 0.7,
      amounts: [5.99],
      role: "PRICE",
      guard: null,
      prior: null,
      outcome: "item",
    },
  ],
  items: [
    {
      y: 0.7,
      name: "EGGS",
      price: 5.99,
      quantity: null,
      unit_price: null,
      is_discount: false,
      stacked: false,
      name_quality: null,
      line_ids: [2],
      name_word_ids: [[2, 1]],
      price_word_id: [2, 2],
      qty_word_ids: [],
    },
    {
      y: 0.8,
      name: "MILK",
      price: 3.99,
      quantity: null,
      unit_price: null,
      is_discount: false,
      stacked: false,
      name_quality: null,
      line_ids: [1],
      name_word_ids: [[1, 1]],
      price_word_id: [1, 2],
      qty_word_ids: [],
    },
  ],
  dropped_items: [],
  summary: { subtotal: 9.98, tax: null, grand_total: 10.5 },
  reconcile: {
    status: "match",
    item_sum: 9.98,
    baseline: 9.98,
    baseline_source: "subtotal",
    baseline_figures_agreeing: 1,
  },
  ...overrides,
});

describe("buildStagePlan", () => {
  it("always includes zone, bands, pair, reconcile in order", () => {
    const plan = buildStagePlan(makeReceipt());
    expect(plan.map((s) => s.key)).toEqual([
      "zone",
      "bands",
      "pair",
      "reconcile",
    ]);
  });

  it("adds guards only when a band was rejected", () => {
    const receipt = makeReceipt();
    receipt.bands.push({
      band_id: 2,
      line_ids: [3],
      word_refs: [[3, 1]],
      text: "VISA DEBIT $9.98",
      y: 0.5,
      amounts: [9.98],
      role: "OUTSIDE",
      guard: "settlement",
      prior: null,
    });
    const plan = buildStagePlan(receipt);
    expect(plan.map((s) => s.key)).toContain("guards");
    expect(plan.findIndex((s) => s.key === "guards")).toBeLessThan(
      plan.findIndex((s) => s.key === "pair")
    );
  });

  it("adds qty when an item carries a printed quantity", () => {
    const receipt = makeReceipt();
    receipt.items[0].quantity = 2;
    receipt.items[0].unit_price = 3.0;
    const plan = buildStagePlan(receipt);
    expect(plan.map((s) => s.key)).toContain("qty");
  });
});

describe("computeFrame", () => {
  const plan = buildStagePlan(makeReceipt());

  it("starts in the first stage with zero progress", () => {
    const frame = computeFrame(plan, 0);
    expect(frame.stageIndex).toBe(0);
    expect(frame.stageKey).toBe("zone");
    expect(frame.progress).toBe(0);
    expect(frame.phase).toBe("stages");
  });

  it("advances through stages by cumulative duration", () => {
    const frame = computeFrame(plan, plan[0].duration + 1);
    expect(frame.stageIndex).toBe(1);
    expect(frame.stageKey).toBe("bands");
    expect(frame.progress).toBeGreaterThanOrEqual(0);
    expect(frame.progress).toBeLessThan(0.1);
  });

  it("holds on the final stage, then transitions, then advances", () => {
    const total = planDuration(plan);
    expect(computeFrame(plan, total + 1).phase).toBe("hold");
    expect(computeFrame(plan, total + HOLD_DURATION + 1).phase).toBe(
      "transition"
    );
    expect(
      computeFrame(plan, total + HOLD_DURATION + TRANSITION_DURATION + 1)
        .phase
    ).toBe("next");
  });
});

describe("revealCount", () => {
  const plan = buildStagePlan(makeReceipt());

  it("reveals nothing before the stage, everything after", () => {
    const before = computeFrame(plan, 0);
    expect(revealCount(5, plan, before, "pair")).toBe(0);
    const total = planDuration(plan);
    const after = computeFrame(plan, total + 1);
    expect(revealCount(5, plan, after, "pair")).toBe(5);
  });

  it("reveals progressively during the stage", () => {
    const pairStart =
      plan[0].duration + plan[1].duration + plan[2].duration / 2;
    const during = computeFrame(plan, pairStart);
    expect(during.stageKey).toBe("pair");
    const revealed = revealCount(4, plan, during, "pair");
    expect(revealed).toBeGreaterThan(0);
    expect(revealed).toBeLessThanOrEqual(4);
  });

  it("returns the full count for stages missing from the plan", () => {
    const frame = computeFrame(plan, 0);
    expect(revealCount(3, plan, frame, "qty")).toBe(3);
  });
});

describe("stage predicates", () => {
  const plan = buildStagePlan(makeReceipt());

  it("stageStarted / stageDone bracket the running stage", () => {
    const midBands = computeFrame(
      plan,
      plan[0].duration + plan[1].duration / 2
    );
    expect(stageStarted(plan, midBands, "bands")).toBe(true);
    expect(stageDone(plan, midBands, "bands")).toBe(false);
    expect(stageDone(plan, midBands, "zone")).toBe(true);
    expect(stageStarted(plan, midBands, "pair")).toBe(false);
  });
});

describe("displayItems", () => {
  it("sorts items top-of-receipt first (descending y)", () => {
    const items = displayItems(makeReceipt());
    expect(items.map((i) => i.name)).toEqual(["MILK", "EGGS"]);
  });
});
