import fs from "fs";
import path from "path";
import {
  buildLabelBoxes,
  ShowcaseLabelFile,
} from "../AugmentationShowcase/labelGeometry";
import { MERCHANTS } from "./pipelineData";

/**
 * Regression for the Costco act-8 NaN bug: Costco's `final.labels.json` carries
 * `metadata.render` WITHOUT a `margin` field (sprouts has margin:10). The
 * margin-aware transform computed `W - 2*margin` = NaN, so every ground-truth
 * rect rendered as `x/y/width/height = "NaN"`. buildLabelBoxes must produce
 * finite rects for BOTH merchants' real, committed label files.
 */

const PIPELINE_DIR = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/pipeline",
);

const loadFinalLabels = (merchant: string): ShowcaseLabelFile | null => {
  const file = path.join(PIPELINE_DIR, merchant, "final.labels.json");
  if (!fs.existsSync(file)) {
    return null;
  }
  return JSON.parse(fs.readFileSync(file, "utf-8")) as ShowcaseLabelFile;
};

describe("final.labels.json -> label boxes (both merchants)", () => {
  test.each(MERCHANTS)(
    "%s: every computed rect is finite (no NaN from a missing margin)",
    (merchant) => {
      const labels = loadFinalLabels(merchant);
      if (!labels) {
        // Asset not generated for this merchant yet — nothing to regress.
        return;
      }
      const boxes = buildLabelBoxes(labels);
      expect(boxes.length).toBeGreaterThan(0);
      boxes.forEach((box) => {
        expect(Number.isFinite(box.rect.left)).toBe(true);
        expect(Number.isFinite(box.rect.top)).toBe(true);
        expect(Number.isFinite(box.rect.width)).toBe(true);
        expect(Number.isFinite(box.rect.height)).toBe(true);
      });
    },
  );

  test("a render without margin maps labels to the full-image span", () => {
    // margin absent must behave exactly like the plain full-span transform.
    const file: ShowcaseLabelFile = {
      tokens: ["A"],
      ner_tags: ["B-MERCHANT_NAME"],
      bboxes: [[100, 200, 300, 400]],
      metadata: { render: { width: 760, height: 3222 } },
    };
    const [box] = buildLabelBoxes(file);
    // x0/10, (1000 - y1)/10, (x1-x0)/10, (y1-y0)/10
    expect(box.rect.left).toBeCloseTo(10, 6);
    expect(box.rect.top).toBeCloseTo((1000 - 400) / 10, 6);
    expect(box.rect.width).toBeCloseTo(20, 6);
    expect(box.rect.height).toBeCloseTo(20, 6);
  });
});
