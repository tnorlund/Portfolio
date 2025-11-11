[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / computeFinalReceiptTilt

# Function: computeFinalReceiptTilt()

> **computeFinalReceiptTilt**(`lines`, `hull`, `centroid`, `avgAngle`): `number`

Defined in: [utils/receipt/boundingBox.ts:267](https://github.com/tnorlund/Portfolio/blob/31c1eac3789fe1cf0f75d7c9e510a1b4f95c35c7/portfolio/utils/receipt/boundingBox.ts#L267)

Compute the final tilt angle of the receipt by analyzing text line edges.

This function refines the average text angle by examining the top and bottom
edges of the text lines. It uses the Theil-Sen estimator to compute robust
slope estimates from the edge points and returns the average of the resulting
angles.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines detected on the receipt image.

### hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Convex hull points of the receipt.

### centroid

[`Point`](../../../../types/api/interfaces/Point.md)

Centroid of the receipt hull.

### avgAngle

`number`

Initial average text angle in degrees as fallback.

## Returns

`number`

Refined tilt angle in degrees, or original avgAngle if computation fails.
