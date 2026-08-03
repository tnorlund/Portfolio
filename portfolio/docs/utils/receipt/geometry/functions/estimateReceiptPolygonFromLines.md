[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/geometry](../README.md) / estimateReceiptPolygonFromLines

# Function: estimateReceiptPolygonFromLines()

> **estimateReceiptPolygonFromLines**(`lines`): \{ `bottom_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `bottom_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); `receipt_id`: `string`; `top_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `top_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); \} \| `null`

Defined in: [utils/receipt/geometry.ts:120](https://github.com/tnorlund/Portfolio/blob/b52af8d8c0b4f3796b5bdff13b2662e5305f19bd/portfolio/utils/receipt/geometry.ts#L120)

Estimate a receipt polygon when only OCR line data is available.

The function computes left and right edges from the lines and uses
those to build a four point polygon. If either edge cannot be
determined, `null` is returned.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines belonging to the receipt.

## Returns

\{ `bottom_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `bottom_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); `receipt_id`: `string`; `top_left`: [`Point`](../../../geometry/basic/interfaces/Point.md); `top_right`: [`Point`](../../../geometry/basic/interfaces/Point.md); \} \| `null`

The estimated receipt polygon or `null`.
