import fs from "fs";
import path from "path";
import {
  buildLabelBoxes,
  familiesIn,
  familyColors,
  familyOf,
  ShowcaseLabelFile,
  toCssRect,
  toCssRectInner,
} from "./labelGeometry";
import { MERCHANTS, PIPELINE_MERCHANT } from "./pipelineData";

const PIPELINE_DIR = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/pipeline",
);

const loadFinalLabels = (merchant: string): ShowcaseLabelFile =>
  JSON.parse(
    fs.readFileSync(
      path.join(PIPELINE_DIR, merchant, "final.labels.json"),
      "utf-8",
    ),
  );

describe("familyOf", () => {
  test.each([
    ["B-PRODUCT_NAME", "PRODUCT_NAME"],
    ["I-GRAND_TOTAL", "GRAND_TOTAL"],
    ["O", null],
    ["", null],
  ])("%s -> %s", (tag, expected) => {
    expect(familyOf(tag)).toBe(expected);
  });
});

describe("toCssRect", () => {
  test("flips the y axis (LayoutLM y-up -> CSS y-down)", () => {
    // A box at the very top of the receipt (y near 1000).
    const rect = toCssRect([271, 968, 821, 999]);
    expect(rect.left).toBeCloseTo(27.1);
    expect(rect.top).toBeCloseTo(0.1);
    expect(rect.width).toBeCloseTo(55.0);
    expect(rect.height).toBeCloseTo(3.1);
  });

  test("a box at the bottom lands near top=100%", () => {
    const rect = toCssRect([0, 0, 100, 20]);
    expect(rect.top).toBeCloseTo(98);
    expect(rect.height).toBeCloseTo(2);
  });
});

describe("toCssRectInner", () => {
  test("insets the box by the render margin", () => {
    // 100x100 render with a 10px margin: label space spans the inner 80x80.
    const rect = toCssRectInner([0, 0, 1000, 1000], {
      width: 100,
      height: 100,
      margin: 10,
    });
    expect(rect.left).toBeCloseTo(10);
    expect(rect.top).toBeCloseTo(10);
    expect(rect.width).toBeCloseTo(80);
    expect(rect.height).toBeCloseTo(80);
  });

  test("a missing margin is treated as zero, not NaN", () => {
    const rect = toCssRectInner([100, 200, 300, 400], {
      width: 760,
      height: 2000,
    });
    expect(rect).toEqual(toCssRect([100, 200, 300, 400]));
  });
});

describe("real pipeline label files", () => {
  test("buildLabelBoxes only emits labeled tokens with valid geometry", () => {
    const file = loadFinalLabels(PIPELINE_MERCHANT);
    const boxes = buildLabelBoxes(file);
    const labeled = file.ner_tags.filter((t) => t !== "O").length;
    expect(boxes).toHaveLength(labeled);
    boxes.forEach((box) => {
      expect(box.rect.left).toBeGreaterThanOrEqual(0);
      expect(box.rect.top).toBeGreaterThanOrEqual(0);
      expect(box.rect.left + box.rect.width).toBeLessThanOrEqual(100.01);
      expect(box.rect.top + box.rect.height).toBeLessThanOrEqual(100.01);
    });
  });

  test("the pipeline receipt's merchant name is at the top of the image", () => {
    const file = loadFinalLabels(PIPELINE_MERCHANT);
    const merchant = buildLabelBoxes(file).find(
      (b) => b.family === "MERCHANT_NAME",
    );
    expect(merchant).toBeDefined();
    expect(merchant!.rect.top).toBeLessThan(10);
  });

  test("buildLabelBoxes skips malformed bboxes instead of emitting them", () => {
    const file: ShowcaseLabelFile = {
      tokens: ["A", "B", "C"],
      ner_tags: ["B-MERCHANT_NAME", "B-DATE", "O"],
      bboxes: [[0, 900, 100, 1000], [0, 0, 100], [0, 0, 10, 10]],
    };
    const boxes = buildLabelBoxes(file);
    expect(boxes.map((b) => b.token)).toEqual(["A"]);
  });

  test("familiesIn lists each family once, in first-appearance order", () => {
    const file: ShowcaseLabelFile = {
      tokens: ["A", "B", "C", "D"],
      ner_tags: ["B-MERCHANT_NAME", "I-MERCHANT_NAME", "O", "B-DATE"],
      bboxes: [],
    };
    expect(familiesIn(file)).toEqual(["MERCHANT_NAME", "DATE"]);
  });
});

describe("familyColors", () => {
  test("uses the shared palette and falls back for unknown families", () => {
    const colors = familyColors([
      "PRODUCT_NAME",
      "SOMETHING_NEW",
      "GRAND_TOTAL",
    ]);
    expect(colors.PRODUCT_NAME).toBe("var(--color-purple)");
    expect(colors.GRAND_TOTAL).toBe("var(--color-green)");
    expect(colors.SOMETHING_NEW).toMatch(/^var\(--color-/);
  });

  test.each(MERCHANTS)(
    "%s: every family in the committed label file gets a color",
    (merchant) => {
      const families = familiesIn(loadFinalLabels(merchant));
      const colors = familyColors(families);
      families.forEach((family) =>
        expect(colors[family]).toMatch(/^(var\(--color-|color-mix\()/),
      );
    },
  );
});
