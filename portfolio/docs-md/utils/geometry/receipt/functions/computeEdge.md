[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeEdge

# Function: computeEdge()

> **computeEdge**(`lines`, `pick`, `bins`): `null` \| \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \}

Defined in: [utils/geometry/receipt.ts:128](https://github.com/tnorlund/Portfolio/blob/bcc8269a88b4909082c9fd8d4e4e7c7929beb1ce/portfolio/utils/geometry/receipt.ts#L128)

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
