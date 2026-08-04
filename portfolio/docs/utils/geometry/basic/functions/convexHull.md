[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / convexHull

# Function: convexHull()

> **convexHull**(`points`): [`Point`](../interfaces/Point.md)[]

Defined in: [utils/geometry/basic.ts:20](https://github.com/tnorlund/Portfolio/blob/7e3cc84a18844719516f6eb4810b744e3a7d392b/portfolio/utils/geometry/basic.ts#L20)

Compute the convex hull of a set of points using a monotone chain
algorithm.

## Parameters

### points

[`Point`](../interfaces/Point.md)[]

Points to compute the hull for.

## Returns

[`Point`](../interfaces/Point.md)[]

An array of points describing the outer hull in
counter‑clockwise order.
