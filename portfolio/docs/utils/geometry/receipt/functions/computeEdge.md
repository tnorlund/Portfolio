[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeEdge

# Function: computeEdge()

> **computeEdge**(`lines`, `pick`, `bins`): \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \} \| `null`

Defined in: [utils/geometry/receipt.ts:128](https://github.com/tnorlund/Portfolio/blob/ae8730e1232da24364a117560f0abc29fa5e6ca3/portfolio/utils/geometry/receipt.ts#L128)

Estimate a straight edge from OCR line data.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

Detected OCR lines for the image.

### pick

Whether to compute the `"left"` or `"right"` edge.

`"left"` | `"right"`

### bins

`number` = `6`

Number of vertical bins to reduce the point cloud.

## Returns

\{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \} \| `null`

The approximated edge or `null` if there are not enough
samples.
