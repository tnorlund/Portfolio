/**
 * Regression: Costco's act-8 label rects rendered NaN because its labels
 * file lacked metadata.render.margin (found by interactive browser QA).
 * Every rect from BOTH merchants' real label files must be finite.
 */
import fs from "fs";
import path from "path";
import {
  buildLabelBoxes,
  ShowcaseLabelFile,
} from "../AugmentationShowcase/labelGeometry";

const PIPELINE = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/pipeline",
);

describe.each(["sprouts", "costco"])("%s final labels", (merchant) => {
  const file: ShowcaseLabelFile = JSON.parse(
    fs.readFileSync(path.join(PIPELINE, merchant, "final.labels.json"), "utf-8"),
  );

  test("every labeled token yields a finite rect", () => {
    const boxes = buildLabelBoxes(file);
    const labeled = file.ner_tags.filter((t) => t !== "O").length;
    expect(boxes.length).toBeGreaterThan(0);
    expect(boxes.length).toBe(labeled);
    boxes.forEach(({ rect }) => {
      [rect.left, rect.top, rect.width, rect.height].forEach((v) => {
        expect(Number.isFinite(v)).toBe(true);
      });
      expect(rect.width).toBeGreaterThan(0);
      expect(rect.height).toBeGreaterThan(0);
    });
  });

  test("a missing margin degrades to finite rects (never NaN)", () => {
    const stripped = {
      ...file,
      metadata: {
        ...file.metadata,
        render: {
          width: file.metadata.render.width,
          height: file.metadata.render.height,
        },
      },
    } as ShowcaseLabelFile;
    buildLabelBoxes(stripped).forEach(({ rect }) => {
      expect(Number.isFinite(rect.left)).toBe(true);
      expect(Number.isFinite(rect.height)).toBe(true);
    });
  });
});
