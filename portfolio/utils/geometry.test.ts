const assert = require("assert");
import {
  computeHullCentroid,
  theilSen,
  computeHullEdge,
  computeEdge,
  computeReceiptBoxFromLineEdges,
  Point,
} from './geometry';

function run() {
  // centroid of unit square
  const hull: Point[] = [
    { x: 0, y: 0 },
    { x: 1, y: 0 },
    { x: 1, y: 1 },
    { x: 0, y: 1 },
  ];
  const c = computeHullCentroid(hull);
  assert(Math.abs(c.x - 0.5) < 1e-6);
  assert(Math.abs(c.y - 0.5) < 1e-6);

  // theilSen on diagonal
  const pts: Point[] = [
    { x: 0, y: 0 },
    { x: 1, y: 1 },
    { x: 2, y: 2 },
  ];
  const ts = theilSen(pts);
  assert(Math.abs(ts.slope - 1) < 1e-6);
  assert(Math.abs(ts.intercept) < 1e-6);

  // hull edge
  const hull2: Point[] = [
    { x: 0, y: 0 },
    { x: 2, y: 0 },
    { x: 2, y: 1 },
    { x: 0, y: 1 },
  ];
  const edge = computeHullEdge(hull2, 4, 'right');
  assert(edge !== null);
  if (edge) {
    assert(Math.abs(edge.top.x - 2) < 1e-6);
    assert(Math.abs(edge.bottom.x - 2) < 1e-6);
  }

  // computeEdge
  const lines = [
    {
      top_left: { x: 0, y: 1 },
      top_right: { x: 1, y: 1 },
      bottom_left: { x: 0, y: 0 },
      bottom_right: { x: 1, y: 0 },
    },
    {
      top_left: { x: 1, y: 1 },
      top_right: { x: 2, y: 1 },
      bottom_left: { x: 1, y: 0 },
      bottom_right: { x: 2, y: 0 },
    },
  ];
  const edge2 = computeEdge(lines as any, 'right');
  assert(edge2 !== null);

  const centroid: Point = { x: 1.5, y: 0.5 };
  const box = computeReceiptBoxFromLineEdges(lines as any, hull2, centroid, 0);
  assert.strictEqual(box.length, 4);
}

run();
