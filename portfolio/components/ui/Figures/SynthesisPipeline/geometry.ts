/**
 * Pure geometry for the SynthesisPipeline figure.
 *
 * The glyph skeletons committed under
 * `public/synthetic-receipts/pipeline/<merchant>/char_skeleton.json` describe
 * the pen path a thermal printhead travels for one character. Coordinates are
 * in CAP UNITS: the baseline sits at y=0 and the cap height at y=CAP_UNITS
 * (1000), with y pointing UP. Every consumer here (SVG paths, dot stamping)
 * works in SVG space, so we flip y once on the way in: `svgY = CAP_UNITS - y`.
 *
 * A stroke is a list of nodes; each node may carry `hIn` / `hOut` absolute
 * Bézier handle coordinates. The segment between two consecutive nodes is a
 * cubic Bézier when either endpoint offers a handle (control1 = A.hOut ?? A,
 * control2 = B.hIn ?? B); with no handles it is a straight line.
 */

export const CAP_UNITS = 1000;

export interface Point {
  x: number;
  y: number;
}

export interface SkeletonHandle {
  x: number;
  y: number;
}

export interface SkeletonNode {
  x: number;
  y: number;
  type?: string;
  hIn?: SkeletonHandle;
  hOut?: SkeletonHandle;
}

export interface SkeletonStroke {
  closed?: boolean;
  nodes: SkeletonNode[];
}

export interface GlyphSkeleton {
  version?: number;
  char: string;
  codepoint?: number;
  width?: number;
  strokes: SkeletonStroke[];
}

/** One path segment already flipped into SVG (y-down) space. */
export interface PathSegment {
  type: "L" | "C";
  p0: Point;
  p1: Point;
  /** Cubic control points (only for type "C"). */
  c1?: Point;
  c2?: Point;
}

export interface ViewBox {
  minX: number;
  minY: number;
  width: number;
  height: number;
}

/** Flip a cap-unit (y-up) point into SVG (y-down) space. */
export const flipY = (pt: Point, capUnits: number = CAP_UNITS): Point => ({
  x: pt.x,
  y: capUnits - pt.y,
});

/**
 * Build one stroke's segments, mapping every anchor + handle through `tf`.
 * A segment is cubic when either endpoint contributes a handle. The transform
 * decides the target space (y-flip for SVG, cloud-pixel mapping for act 3/4).
 */
const buildSegments = (
  stroke: SkeletonStroke,
  tf: (pt: Point) => Point,
): PathSegment[] => {
  const nodes = stroke.nodes ?? [];
  if (nodes.length < 2) {
    return [];
  }
  const segments: PathSegment[] = [];

  const pairs: Array<[SkeletonNode, SkeletonNode]> = [];
  for (let i = 0; i < nodes.length - 1; i += 1) {
    pairs.push([nodes[i], nodes[i + 1]]);
  }
  if (stroke.closed && nodes.length > 2) {
    pairs.push([nodes[nodes.length - 1], nodes[0]]);
  }

  pairs.forEach(([a, b]) => {
    const p0 = tf(a);
    const p1 = tf(b);
    const hasCurve = Boolean(a.hOut || b.hIn);
    if (hasCurve) {
      segments.push({
        type: "C",
        p0,
        c1: tf(a.hOut ?? a),
        c2: tf(b.hIn ?? b),
        p1,
      });
    } else {
      segments.push({ type: "L", p0, p1 });
    }
  });
  return segments;
};

/**
 * Build the SVG segments for one stroke, already flipped into y-down space.
 * A segment is cubic when either endpoint contributes a handle.
 */
export const strokeSegments = (
  stroke: SkeletonStroke,
  capUnits: number = CAP_UNITS,
): PathSegment[] => buildSegments(stroke, (pt) => flipY(pt, capUnits));

/** All SVG segments for every stroke in a skeleton. */
export const skeletonSegments = (
  skeleton: GlyphSkeleton,
  capUnits: number = CAP_UNITS,
): PathSegment[][] =>
  (skeleton.strokes ?? []).map((stroke) =>
    strokeSegments(stroke, capUnits),
  );

/** SVG path `d` string for a single stroke's segments. */
export const segmentsToPathD = (segments: PathSegment[]): string => {
  if (segments.length === 0) {
    return "";
  }
  const round = (n: number) => Math.round(n * 100) / 100;
  const start = segments[0].p0;
  let d = `M ${round(start.x)} ${round(start.y)}`;
  segments.forEach((seg) => {
    if (seg.type === "C" && seg.c1 && seg.c2) {
      d +=
        ` C ${round(seg.c1.x)} ${round(seg.c1.y)}` +
        ` ${round(seg.c2.x)} ${round(seg.c2.y)}` +
        ` ${round(seg.p1.x)} ${round(seg.p1.y)}`;
    } else {
      d += ` L ${round(seg.p1.x)} ${round(seg.p1.y)}`;
    }
  });
  return d;
};

/** One `d` string per stroke. */
export const skeletonPathDs = (
  skeleton: GlyphSkeleton,
  capUnits: number = CAP_UNITS,
): string[] =>
  skeletonSegments(skeleton, capUnits).map((segs) => segmentsToPathD(segs));

/** Point on a cubic Bézier at parameter t in [0, 1]. */
export const cubicPoint = (
  p0: Point,
  c1: Point,
  c2: Point,
  p1: Point,
  t: number,
): Point => {
  const mt = 1 - t;
  const a = mt * mt * mt;
  const b = 3 * mt * mt * t;
  const c = 3 * mt * t * t;
  const d = t * t * t;
  return {
    x: a * p0.x + b * c1.x + c * c2.x + d * p1.x,
    y: a * p0.y + b * c1.y + c * c2.y + d * p1.y,
  };
};

/**
 * Flatten one segment into a polyline. Lines yield their two endpoints; cubics
 * are sampled at `samplesPerSeg` intervals (the leading endpoint is emitted so
 * that concatenating segments does not duplicate shared points).
 */
export const flattenSegment = (
  seg: PathSegment,
  samplesPerSeg: number = 40,
): Point[] => {
  if (seg.type === "L" || !seg.c1 || !seg.c2) {
    return [seg.p0, seg.p1];
  }
  const pts: Point[] = [];
  for (let i = 0; i <= samplesPerSeg; i += 1) {
    pts.push(cubicPoint(seg.p0, seg.c1, seg.c2, seg.p1, i / samplesPerSeg));
  }
  return pts;
};

/** Flatten a whole stroke into one polyline (no duplicated join points). */
export const flattenStroke = (
  segments: PathSegment[],
  samplesPerSeg: number = 40,
): Point[] => {
  const out: Point[] = [];
  segments.forEach((seg, idx) => {
    const pts = flattenSegment(seg, samplesPerSeg);
    // Drop the leading point of every segment after the first: it equals the
    // previous segment's trailing point.
    (idx === 0 ? pts : pts.slice(1)).forEach((p) => out.push(p));
  });
  return out;
};

const dist = (a: Point, b: Point): number =>
  Math.hypot(b.x - a.x, b.y - a.y);

/** Total length of a polyline. */
export const polylineLength = (points: Point[]): number => {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    total += dist(points[i - 1], points[i]);
  }
  return total;
};

/**
 * Resample a polyline at a fixed arc-length `step`, walking from the start.
 * Deterministic for given inputs: the count is
 * `floor(totalLength / step) + 1` and always includes the first point. Used to
 * place thermal dots evenly along the pen path.
 */
export const resampleByArcLength = (
  points: Point[],
  step: number,
): Point[] => {
  if (points.length === 0 || step <= 0) {
    return points.slice(0, 1);
  }
  const out: Point[] = [points[0]];
  let nextAt = step;
  let travelled = 0;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const segLen = dist(a, b);
    if (segLen === 0) {
      continue;
    }
    while (travelled + segLen >= nextAt) {
      const t = (nextAt - travelled) / segLen;
      out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
      nextAt += step;
    }
    travelled += segLen;
  }
  return out;
};

/**
 * Arc-length-even dot centers across every stroke of a glyph, in SVG space.
 * Deterministic: same skeleton + step + samplesPerSeg always yields the same
 * ordered point list, so the canvas stamping is reproducible.
 */
export const glyphDotPoints = (
  skeleton: GlyphSkeleton,
  step: number,
  options: { capUnits?: number; samplesPerSeg?: number } = {},
): Point[] => {
  const { capUnits = CAP_UNITS, samplesPerSeg = 40 } = options;
  const points: Point[] = [];
  skeletonSegments(skeleton, capUnits).forEach((segments) => {
    if (segments.length === 0) {
      return;
    }
    const polyline = flattenStroke(segments, samplesPerSeg);
    resampleByArcLength(polyline, step).forEach((p) => points.push(p));
  });
  return points;
};

/**
 * Tight view box around every anchor AND handle of a skeleton (SVG space),
 * padded so stroked paths and handle dots are not clipped.
 */
export const skeletonViewBox = (
  skeleton: GlyphSkeleton,
  options: { capUnits?: number; padding?: number } = {},
): ViewBox => {
  const { capUnits = CAP_UNITS, padding = 80 } = options;
  const xs: number[] = [];
  const ys: number[] = [];
  const consider = (pt: Point) => {
    const f = flipY(pt, capUnits);
    xs.push(f.x);
    ys.push(f.y);
  };
  (skeleton.strokes ?? []).forEach((stroke) => {
    (stroke.nodes ?? []).forEach((node) => {
      consider(node);
      if (node.hIn) consider(node.hIn);
      if (node.hOut) consider(node.hOut);
    });
  });
  if (xs.length === 0) {
    return { minX: 0, minY: 0, width: capUnits, height: capUnits };
  }
  const minX = Math.min(...xs) - padding;
  const minY = Math.min(...ys) - padding;
  const maxX = Math.max(...xs) + padding;
  const maxY = Math.max(...ys) + padding;
  return {
    minX,
    minY,
    width: maxX - minX,
    height: maxY - minY,
  };
};

export interface GlyphAnchors {
  /** Node anchor points, in SVG space. */
  anchors: Point[];
  /** Handle segments (anchor -> control), in SVG space. */
  handles: Array<{ from: Point; to: Point }>;
}

/** Anchor dots + handle lines for the pen-path act, flipped into SVG space. */
export const glyphAnchors = (
  skeleton: GlyphSkeleton,
  capUnits: number = CAP_UNITS,
): GlyphAnchors => {
  const anchors: Point[] = [];
  const handles: Array<{ from: Point; to: Point }> = [];
  (skeleton.strokes ?? []).forEach((stroke) => {
    (stroke.nodes ?? []).forEach((node) => {
      const anchor = flipY(node, capUnits);
      anchors.push(anchor);
      if (node.hIn) {
        handles.push({ from: anchor, to: flipY(node.hIn, capUnits) });
      }
      if (node.hOut) {
        handles.push({ from: anchor, to: flipY(node.hOut, capUnits) });
      }
    });
  });
  return { anchors, handles };
};

/** Total node count across all strokes (for the "N nodes" callout). */
export const nodeCount = (skeleton: GlyphSkeleton): number =>
  (skeleton.strokes ?? []).reduce(
    (sum, stroke) => sum + (stroke.nodes?.length ?? 0),
    0,
  );

/* ==================================================================== */
/* Cloud-pixel alignment                                                 */
/*                                                                       */
/* The consensus cloud PNG has its own pixel geometry (measured in       */
/* dot_params.cloudGeom). To overlay the skeleton on the cloud without   */
/* drift, map cap-unit coordinates straight into cloud-pixel space and   */
/* draw both in the same box. This mapping already performs the y-flip.  */
/* ==================================================================== */

export interface CloudGeom {
  imageW: number;
  imageH: number;
  baselineFromBottomPx: number;
  capHeightPx: number;
  inkCenterXPx: number;
}

/** Cloud pixels per cap unit. */
export const cloudScale = (cloud: CloudGeom): number =>
  cloud.capHeightPx / CAP_UNITS;

const glyphWidthOf = (skeleton: GlyphSkeleton): number =>
  skeleton.width && skeleton.width > 0 ? skeleton.width : CAP_UNITS;

/**
 * Map a cap-unit point (y-up, baseline 0) into the cloud PNG's pixel space:
 *   x_px = inkCenterXPx + (x - glyphWidth/2) · scale
 *   y_px = imageH - baselineFromBottomPx - y · scale
 */
export const mapToCloud = (
  pt: Point,
  cloud: CloudGeom,
  glyphWidth: number,
): Point => {
  const s = cloudScale(cloud);
  return {
    x: cloud.inkCenterXPx + (pt.x - glyphWidth / 2) * s,
    y: cloud.imageH - cloud.baselineFromBottomPx - pt.y * s,
  };
};

/** Segments per stroke, mapped into cloud-pixel space. */
export const skeletonSegmentsCloud = (
  skeleton: GlyphSkeleton,
  cloud: CloudGeom,
): PathSegment[][] => {
  const gw = glyphWidthOf(skeleton);
  return (skeleton.strokes ?? []).map((stroke) =>
    buildSegments(stroke, (pt) => mapToCloud(pt, cloud, gw)),
  );
};

/** One SVG `d` string per stroke, in cloud-pixel space. */
export const skeletonPathDsCloud = (
  skeleton: GlyphSkeleton,
  cloud: CloudGeom,
): string[] => skeletonSegmentsCloud(skeleton, cloud).map(segmentsToPathD);

/** Anchor dots + handle lines, in cloud-pixel space. */
export const glyphAnchorsCloud = (
  skeleton: GlyphSkeleton,
  cloud: CloudGeom,
): GlyphAnchors => {
  const gw = glyphWidthOf(skeleton);
  const anchors: Point[] = [];
  const handles: Array<{ from: Point; to: Point }> = [];
  (skeleton.strokes ?? []).forEach((stroke) => {
    (stroke.nodes ?? []).forEach((node) => {
      const anchor = mapToCloud(node, cloud, gw);
      anchors.push(anchor);
      if (node.hIn) {
        handles.push({ from: anchor, to: mapToCloud(node.hIn, cloud, gw) });
      }
      if (node.hOut) {
        handles.push({ from: anchor, to: mapToCloud(node.hOut, cloud, gw) });
      }
    });
  });
  return { anchors, handles };
};

/**
 * Arc-length-even dot centers in cloud-pixel space. `stepUnits` is the spacing
 * in cap units (so density matches the act-3 skeleton); it is converted to
 * pixels via the cloud scale.
 */
export const glyphDotPointsCloud = (
  skeleton: GlyphSkeleton,
  cloud: CloudGeom,
  stepUnits: number,
  samplesPerSeg: number = 40,
): Point[] => {
  const stepPx = stepUnits * cloudScale(cloud);
  const points: Point[] = [];
  skeletonSegmentsCloud(skeleton, cloud).forEach((segments) => {
    if (segments.length === 0) {
      return;
    }
    const polyline = flattenStroke(segments, samplesPerSeg);
    resampleByArcLength(polyline, stepPx).forEach((p) => points.push(p));
  });
  return points;
};
