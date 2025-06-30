[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeEdge

# Function: computeEdge()

> **computeEdge**(`lines`, `pick`, `bins`): `null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

Defined in: [utils/geometry/receipt.ts:128](https://github.com/tnorlund/Portfolio/blob/5973e699aa6d8ac4a7c7a1f66fe3bd1b5b6aef45/portfolio/utils/geometry/receipt.ts#L128)

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
