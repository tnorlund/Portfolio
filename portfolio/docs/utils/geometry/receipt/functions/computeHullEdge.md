[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeHullEdge

# Function: computeHullEdge()

> **computeHullEdge**(`hull`, `bins`, `pick`): `null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

Defined in: [utils/geometry/receipt.ts:96](https://github.com/tnorlund/Portfolio/blob/c20e64db87f42c98cf4a39e6b2073e63672a59f5/portfolio/utils/geometry/receipt.ts#L96)

Sample points from a convex hull to estimate the left or right edge
as a straight line.

## Parameters

### hull

[`Point`](../../basic/interfaces/Point.md)[]

Polygon points representing the convex hull.

### bins

`number` = `12`

Number of vertical bins used to sample representative
points.

### pick

Which side of the hull to estimate: `"left"` or
`"right"`.

`"left"` | `"right"`

## Returns

`null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

The line through the sampled points or `null` when not
enough samples are available.
