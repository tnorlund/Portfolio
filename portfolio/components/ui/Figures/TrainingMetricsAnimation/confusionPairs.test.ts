import {
  buildPatternHeatmapPlan,
  buildTopConfusionPairs,
  describeSyntheticTarget,
} from "./confusionPairs";

describe("buildTopConfusionPairs", () => {
  test("ranks off-diagonal pairs by count", () => {
    const labels = ["MERCHANT_NAME", "ADDRESS_LINE", "O"];
    const matrix = [
      [20, 2, 7],
      [1, 18, 3],
      [9, 4, 100],
    ];

    const pairs = buildTopConfusionPairs(labels, matrix, 3);

    expect(pairs.map((pair) => pair.id)).toEqual([
      "O->MERCHANT_NAME",
      "MERCHANT_NAME->O",
      "O->ADDRESS_LINE",
    ]);
    expect(pairs[0].share).toBeCloseTo(9 / 113);
  });

  test("ignores diagonal and empty rows", () => {
    const labels = ["DATE", "TIME", "O"];
    const matrix = [
      [8, 0, 0],
      [0, 0, 0],
      [1, 0, 10],
    ];

    const pairs = buildTopConfusionPairs(labels, matrix, 10);

    expect(pairs).toHaveLength(1);
    expect(pairs[0].id).toBe("O->DATE");
  });

  test("breaks count ties with row share", () => {
    const labels = ["DATE", "TIME", "O"];
    const matrix = [
      [90, 10, 0],
      [1, 9, 10],
      [0, 0, 50],
    ];

    const pairs = buildTopConfusionPairs(labels, matrix, 2);

    expect(pairs.map((pair) => pair.id)).toEqual(["TIME->O", "DATE->TIME"]);
  });
});

describe("buildPatternHeatmapPlan", () => {
  test("projects merchant confusions into header and identity heatmap zones", () => {
    const pairs = buildTopConfusionPairs(
      ["MERCHANT_NAME", "ADDRESS_LINE", "O"],
      [
        [20, 0, 8],
        [0, 18, 1],
        [12, 2, 100],
      ],
      2
    );

    const plan = buildPatternHeatmapPlan(pairs);

    expect(plan.cells.map((cell) => cell.zone)).toEqual([
      "header",
      "identity",
    ]);
    expect(plan.cells[0].count).toBe(20);
    expect(plan.cells[0].llmPrompt).toContain("MERCHANT_NAME");
    expect(plan.cells[0].syntheticBrief).toContain("MERCHANT_NAME");
    expect(plan.syntheticReceiptCount).toBeGreaterThan(0);
  });

  test("aggregates price-like swaps into item and total zones", () => {
    const pairs = buildTopConfusionPairs(
      ["LINE_TOTAL", "GRAND_TOTAL", "O"],
      [
        [30, 6, 3],
        [4, 25, 2],
        [1, 5, 120],
      ],
      3
    );

    const plan = buildPatternHeatmapPlan(pairs);
    const totals = plan.cells.find((cell) => cell.zone === "totals");
    const items = plan.cells.find((cell) => cell.zone === "items");

    expect(totals?.count).toBeGreaterThan(0);
    expect(items?.count).toBeGreaterThan(0);
    expect(totals?.intensity).toBeLessThanOrEqual(1);
    expect(items?.pairIds).toContain("LINE_TOTAL->GRAND_TOTAL");
  });
});

describe("describeSyntheticTarget", () => {
  test("targets missed entity variants when prediction is O", () => {
    const target = describeSyntheticTarget("MERCHANT_NAME", "O");

    expect(target.patternTarget).toBe("Missed merchant header");
    expect(target.syntheticTarget).toBe(
      "MERCHANT_NAME examples with varied neighbors"
    );
  });

  test("targets negative lookalikes when actual is O", () => {
    const target = describeSyntheticTarget("O", "ADDRESS_LINE");

    expect(target.patternTarget).toBe("False address block");
    expect(target.syntheticTarget).toBe(
      "Background tokens that resemble ADDRESS"
    );
  });

  test("normalizes BIO prefixes before assigning domain", () => {
    const target = describeSyntheticTarget("B-GRAND_TOTAL", "I-LINE_TOTAL");

    expect(target.patternTarget).toBe("price row swap");
    expect(target.syntheticTarget).toBe(
      "GRAND_TOTAL vs LINE_TOTAL contrast set"
    );
  });
});
