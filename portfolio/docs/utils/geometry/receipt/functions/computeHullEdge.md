[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeHullEdge

# Function: computeHullEdge()

> **computeHullEdge**(`hull`, `bins`, `pick`): `null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

Defined in: [utils/geometry/receipt.ts:96](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/geometry/receipt.ts#L96)

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
