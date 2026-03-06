[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / computeHullCentroid

# Function: computeHullCentroid()

> **computeHullCentroid**(`hull`): [`Point`](../interfaces/Point.md)

Defined in: [utils/geometry/basic.ts:62](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/utils/geometry/basic.ts#L62)

Compute the centroid of a polygon described by its convex hull.

The function falls back to simple averages for degenerate cases such
as a hull with less than three points.

## Parameters

### hull

[`Point`](../interfaces/Point.md)[]

Polygon vertices in counter‑clockwise order.

## Returns

[`Point`](../interfaces/Point.md)

The centroid point of the polygon.
