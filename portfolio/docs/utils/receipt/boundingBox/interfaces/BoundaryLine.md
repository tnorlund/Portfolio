[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / BoundaryLine

# Interface: BoundaryLine

Defined in: [utils/receipt/boundingBox.ts:513](https://github.com/tnorlund/Portfolio/blob/a42aa8dad0618adb0fe0550dfcf0a2e53426c35f/portfolio/utils/receipt/boundingBox.ts#L513)

Represents a boundary line in the receipt detection algorithm.

## Properties

### intercept

> **intercept**: `number`

Defined in: [utils/receipt/boundingBox.ts:523](https://github.com/tnorlund/Portfolio/blob/a42aa8dad0618adb0fe0550dfcf0a2e53426c35f/portfolio/utils/receipt/boundingBox.ts#L523)

Y-intercept for non-vertical lines (y = slope * x + intercept)

***

### isVertical

> **isVertical**: `boolean`

Defined in: [utils/receipt/boundingBox.ts:515](https://github.com/tnorlund/Portfolio/blob/a42aa8dad0618adb0fe0550dfcf0a2e53426c35f/portfolio/utils/receipt/boundingBox.ts#L515)

True if the line is vertical (infinite slope)

***

### slope

> **slope**: `number`

Defined in: [utils/receipt/boundingBox.ts:521](https://github.com/tnorlund/Portfolio/blob/a42aa8dad0618adb0fe0550dfcf0a2e53426c35f/portfolio/utils/receipt/boundingBox.ts#L521)

Slope for non-vertical lines (y = slope * x + intercept)

***

### isInverted?

> `optional` **isInverted**: `boolean`

Defined in: [utils/receipt/boundingBox.ts:517](https://github.com/tnorlund/Portfolio/blob/a42aa8dad0618adb0fe0550dfcf0a2e53426c35f/portfolio/utils/receipt/boundingBox.ts#L517)

True if the line is stored in x = slope * y + intercept form

***

### x?

> `optional` **x**: `number`

Defined in: [utils/receipt/boundingBox.ts:519](https://github.com/tnorlund/Portfolio/blob/a42aa8dad0618adb0fe0550dfcf0a2e53426c35f/portfolio/utils/receipt/boundingBox.ts#L519)

X-coordinate for vertical lines
