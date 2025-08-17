[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/geometry](../README.md) / estimateReceiptPolygonFromLines

# Function: estimateReceiptPolygonFromLines()

> **estimateReceiptPolygonFromLines**(`lines`): `null` \| \{ `bottom_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `bottom_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); `receipt_id`: `string`; `top_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `top_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); \}

Defined in: [utils/receipt/geometry.ts:120](https://github.com/tnorlund/Portfolio/blob/9641e1d9d3137d0e5e68571e3b9c8af7666d753e/portfolio/utils/receipt/geometry.ts#L120)

Estimate a receipt polygon when only OCR line data is available.

The function computes left and right edges from the lines and uses
those to build a four point polygon. If either edge cannot be
determined, `null` is returned.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines belonging to the receipt.

## Returns

`null` \| \{ `bottom_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `bottom_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); `receipt_id`: `string`; `top_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `top_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); \}

The estimated receipt polygon or `null`.
