[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeEdge

# Function: computeEdge()

> **computeEdge**(`lines`, `pick`, `bins`): `null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

Defined in: [utils/geometry/receipt.ts:119](https://github.com/tnorlund/Portfolio/blob/a83016b1a210f1190bf8ba4e72776be7bbd9e3ab/portfolio/utils/geometry/receipt.ts#L119)

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

`null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

The approximated edge or `null` if there are not enough
samples.
