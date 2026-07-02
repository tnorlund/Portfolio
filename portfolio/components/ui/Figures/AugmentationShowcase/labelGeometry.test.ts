import fs from "fs";
import path from "path";
import {
  buildLabelBoxes,
  familiesIn,
  familyColors,
  familyOf,
  findHighlightIndices,
  ShowcaseLabelFile,
  toCssRect,
} from "./labelGeometry";
import { showcaseVariants } from "./showcaseData";

const SHOWCASE_DIR = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/showcase",
);

const loadLabels = (id: string): ShowcaseLabelFile =>
  JSON.parse(
    fs.readFileSync(path.join(SHOWCASE_DIR, `${id}.labels.json`), "utf-8"),
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

describe("real showcase label files", () => {
  test("buildLabelBoxes only emits labeled tokens with valid geometry", () => {
    const file = loadLabels("base");
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

  test("the base receipt's merchant name is at the top of the image", () => {
    const file = loadLabels("base");
    const merchant = buildLabelBoxes(file).find(
      (b) => b.family === "MERCHANT_NAME",
    );
    expect(merchant).toBeDefined();
    expect(merchant!.rect.top).toBeLessThan(10);
  });

  test("add variant highlights the injected item and the new total", () => {
    const variant = showcaseVariants.find(
      (v) => v.id === "add_line_item_1",
    )!;
    const file = loadLabels(variant.id);
    const indices = findHighlightIndices(file, {
      operation: variant.operation,
      itemWords: variant.itemWords,
      newTotal: variant.newTotal,
    });
    const tokens = indices.map((i) => file.tokens[i]);
    expect(tokens).toContain("LIMES");
    expect(tokens.some((t) => t.includes("30.58"))).toBe(true);
  });

  test("remove variant highlights only the recomputed total", () => {
    const variant = showcaseVariants.find(
      (v) => v.id === "remove_line_item_2",
    )!;
    const file = loadLabels(variant.id);
    const indices = findHighlightIndices(file, {
      operation: variant.operation,
      itemWords: variant.itemWords,
      newTotal: variant.newTotal,
    });
    const tokens = indices.map((i) => file.tokens[i]);
    expect(tokens.length).toBeGreaterThan(0);
    expect(tokens.every((t) => t.includes("7.99"))).toBe(true);
    // The removed item must NOT be highlighted (it is absent from the render).
    expect(tokens).not.toContain("SOUR");
  });
});

describe("familyColors", () => {
  test("assigns stable colors and falls back for unknown families", () => {
    const colors = familyColors([
      "PRODUCT_NAME",
      "SOMETHING_NEW",
      "GRAND_TOTAL",
    ]);
    expect(colors.PRODUCT_NAME).toBe("#43A047");
    expect(colors.GRAND_TOTAL).toBe("#E53935");
    expect(colors.SOMETHING_NEW).toMatch(/^#/);
  });
});

describe("showcaseData stays in sync with the committed manifest", () => {
  test("every manifest variant is described with matching totals/tokens", () => {
    const manifest = JSON.parse(
      fs.readFileSync(path.join(SHOWCASE_DIR, "manifest.json"), "utf-8"),
    );
    expect(showcaseVariants).toHaveLength(manifest.variants.length);
    manifest.variants.forEach(
      (mv: {
        variant: string;
        operation: string;
        old_total: string | null;
        new_total: string | null;
        tokens: number;
        labeled_tokens: number;
      }) => {
        const local = showcaseVariants.find((v) => v.id === mv.variant);
        expect(local).toBeDefined();
        expect(local!.operation).toBe(mv.operation);
        expect(local!.oldTotal).toBe(mv.old_total);
        expect(local!.newTotal).toBe(mv.new_total);
        expect(local!.tokens).toBe(mv.tokens);
        expect(local!.labeledTokens).toBe(mv.labeled_tokens);
      },
    );
  });

  test("families used by the label files all get a color", () => {
    showcaseVariants.forEach((variant) => {
      const file = loadLabels(variant.id);
      const families = familiesIn(file);
      const colors = familyColors(families);
      families.forEach((family) =>
        expect(colors[family]).toMatch(/^#[0-9A-Fa-f]{6}$/),
      );
    });
  });
});
