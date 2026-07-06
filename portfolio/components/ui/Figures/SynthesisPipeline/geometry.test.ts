import fs from "fs";
import path from "path";
import {
  CAP_UNITS,
  cubicPoint,
  flattenSegment,
  flattenStroke,
  flipY,
  glyphAnchors,
  glyphDotPoints,
  GlyphSkeleton,
  nodeCount,
  polylineLength,
  resampleByArcLength,
  segmentsToPathD,
  skeletonPathDs,
  skeletonSegments,
  skeletonViewBox,
  strokeSegments,
} from "./geometry";

const SKELETON_PATH = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/pipeline/sprouts/char_skeleton.json",
);

const loadSprouts = (): GlyphSkeleton =>
  JSON.parse(fs.readFileSync(SKELETON_PATH, "utf-8")) as GlyphSkeleton;

describe("flipY", () => {
  test("baseline (y=0) maps to the bottom of the cap box", () => {
    expect(flipY({ x: 10, y: 0 })).toEqual({ x: 10, y: CAP_UNITS });
  });

  test("cap height (y=1000) maps to the top (y=0)", () => {
    expect(flipY({ x: 5, y: 1000 })).toEqual({ x: 5, y: 0 });
  });

  test("x is preserved and y inverts about the cap height", () => {
    expect(flipY({ x: 42, y: 250 })).toEqual({ x: 42, y: 750 });
  });
});

describe("strokeSegments", () => {
  test("a plain two-node stroke with no handles is a line", () => {
    const segs = strokeSegments({
      nodes: [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
      ],
    });
    expect(segs).toHaveLength(1);
    expect(segs[0].type).toBe("L");
    // y flipped: baseline 0 -> CAP_UNITS
    expect(segs[0].p0).toEqual({ x: 0, y: CAP_UNITS });
    expect(segs[0].p1).toEqual({ x: 100, y: CAP_UNITS });
  });

  test("a segment becomes cubic when either endpoint has a handle", () => {
    const segs = strokeSegments({
      nodes: [
        { x: 0, y: 0, hOut: { x: 30, y: 0 } },
        { x: 100, y: 0, hIn: { x: 70, y: 0 } },
      ],
    });
    expect(segs[0].type).toBe("C");
    expect(segs[0].c1).toEqual({ x: 30, y: CAP_UNITS });
    expect(segs[0].c2).toEqual({ x: 70, y: CAP_UNITS });
  });

  test("cubic falls back to the anchor when one handle is absent", () => {
    const segs = strokeSegments({
      nodes: [
        { x: 0, y: 0 }, // no hOut -> c1 falls back to anchor A
        { x: 100, y: 0, hIn: { x: 70, y: 0 } },
      ],
    });
    expect(segs[0].type).toBe("C");
    expect(segs[0].c1).toEqual({ x: 0, y: CAP_UNITS }); // anchor A flipped
    expect(segs[0].c2).toEqual({ x: 70, y: CAP_UNITS });
  });

  test("a closed stroke adds the wrap-around segment", () => {
    const open = strokeSegments({
      nodes: [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
        { x: 50, y: 100 },
      ],
    });
    const closed = strokeSegments({
      closed: true,
      nodes: [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
        { x: 50, y: 100 },
      ],
    });
    expect(open).toHaveLength(2);
    expect(closed).toHaveLength(3);
    // last segment returns to the first node (flipped)
    expect(closed[2].p1).toEqual(flipY({ x: 0, y: 0 }));
  });
});

describe("segmentsToPathD", () => {
  test("emits an M then C/L commands and flips y", () => {
    const d = segmentsToPathD(
      strokeSegments({
        nodes: [
          { x: 0, y: 1000 },
          { x: 100, y: 1000, hIn: { x: 70, y: 1000 } },
          { x: 200, y: 1000 },
        ],
      }),
    );
    // starts at the flipped first node (y 1000 -> 0)
    expect(d.startsWith("M 0 0")).toBe(true);
    expect(d).toContain(" C ");
    expect(d).toContain(" L ");
  });

  test("is deterministic for the committed Sprouts skeleton", () => {
    const skeleton = loadSprouts();
    const a = skeletonPathDs(skeleton);
    const b = skeletonPathDs(skeleton);
    expect(a).toEqual(b);
    expect(a).toHaveLength(skeleton.strokes.length);
    expect(a[0].startsWith("M ")).toBe(true);
  });
});

describe("cubicPoint", () => {
  test("returns the endpoints at t=0 and t=1", () => {
    const p0 = { x: 0, y: 0 };
    const c1 = { x: 0, y: 10 };
    const c2 = { x: 10, y: 10 };
    const p1 = { x: 10, y: 0 };
    expect(cubicPoint(p0, c1, c2, p1, 0)).toEqual(p0);
    expect(cubicPoint(p0, c1, c2, p1, 1)).toEqual(p1);
  });

  test("the midpoint of a symmetric arc bulges toward the controls", () => {
    const mid = cubicPoint(
      { x: 0, y: 0 },
      { x: 0, y: 30 },
      { x: 10, y: 30 },
      { x: 10, y: 0 },
      0.5,
    );
    expect(mid.x).toBeCloseTo(5, 5);
    expect(mid.y).toBeCloseTo(22.5, 5);
  });
});

describe("flattenSegment / flattenStroke", () => {
  test("a line flattens to exactly its two endpoints", () => {
    const pts = flattenSegment({
      type: "L",
      p0: { x: 0, y: 0 },
      p1: { x: 3, y: 4 },
    });
    expect(pts).toEqual([
      { x: 0, y: 0 },
      { x: 3, y: 4 },
    ]);
  });

  test("a cubic flattens to samplesPerSeg + 1 points", () => {
    const pts = flattenSegment(
      {
        type: "C",
        p0: { x: 0, y: 0 },
        c1: { x: 0, y: 10 },
        c2: { x: 10, y: 10 },
        p1: { x: 10, y: 0 },
      },
      12,
    );
    expect(pts).toHaveLength(13);
  });

  test("flattenStroke does not duplicate shared join points", () => {
    const segs = strokeSegments({
      nodes: [
        { x: 0, y: 0 },
        { x: 10, y: 0 },
        { x: 20, y: 0 },
      ],
    });
    // two line segments, 4 endpoints, 1 shared -> 3 unique points
    expect(flattenStroke(segs)).toHaveLength(3);
  });
});

describe("resampleByArcLength", () => {
  test("even count on a straight line of known length", () => {
    const line = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
    ];
    expect(polylineLength(line)).toBe(100);
    const stepped = resampleByArcLength(line, 25);
    // start + at 25,50,75,100 => 5 points
    expect(stepped).toHaveLength(5);
    expect(stepped[0]).toEqual({ x: 0, y: 0 });
    expect(stepped[4].x).toBeCloseTo(100, 6);
  });

  test("is deterministic and step-monotonic", () => {
    const line = [
      { x: 0, y: 0 },
      { x: 0, y: 200 },
    ];
    const a = resampleByArcLength(line, 10);
    const b = resampleByArcLength(line, 10);
    expect(a).toEqual(b);
    const coarse = resampleByArcLength(line, 40);
    expect(coarse.length).toBeLessThan(a.length);
  });

  test("degenerate inputs return a single point", () => {
    expect(resampleByArcLength([], 5)).toEqual([]);
    expect(resampleByArcLength([{ x: 1, y: 2 }], 0)).toEqual([{ x: 1, y: 2 }]);
  });
});

describe("glyphDotPoints", () => {
  test("is deterministic for the committed skeleton", () => {
    const skeleton = loadSprouts();
    const a = glyphDotPoints(skeleton, 60);
    const b = glyphDotPoints(skeleton, 60);
    expect(a).toEqual(b);
    expect(a.length).toBeGreaterThan(10);
  });

  test("a smaller step yields more dots", () => {
    const skeleton = loadSprouts();
    const coarse = glyphDotPoints(skeleton, 120);
    const fine = glyphDotPoints(skeleton, 40);
    expect(fine.length).toBeGreaterThan(coarse.length);
  });
});

describe("skeletonViewBox / glyphAnchors / nodeCount", () => {
  test("view box encloses every flipped anchor and handle", () => {
    const skeleton = loadSprouts();
    const vb = skeletonViewBox(skeleton, { padding: 0 });
    const { anchors, handles } = glyphAnchors(skeleton);
    [...anchors, ...handles.map((h) => h.to)].forEach((pt) => {
      expect(pt.x).toBeGreaterThanOrEqual(vb.minX);
      expect(pt.x).toBeLessThanOrEqual(vb.minX + vb.width);
      expect(pt.y).toBeGreaterThanOrEqual(vb.minY);
      expect(pt.y).toBeLessThanOrEqual(vb.minY + vb.height);
    });
  });

  test("nodeCount counts every node across strokes", () => {
    const skeleton = loadSprouts();
    const total = skeleton.strokes.reduce(
      (s, stroke) => s + stroke.nodes.length,
      0,
    );
    expect(nodeCount(skeleton)).toBe(total);
  });

  test("skeletonSegments yields one segment list per stroke", () => {
    const skeleton = loadSprouts();
    expect(skeletonSegments(skeleton)).toHaveLength(skeleton.strokes.length);
  });
});
