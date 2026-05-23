[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / MilkReceiptData

# Interface: MilkReceiptData

Defined in: [types/api.ts:279](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L279)

## Properties

### bbox

> **bbox**: [`AddressBoundingBox`](AddressBoundingBox.md) \| `null`

Defined in: [types/api.ts:295](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L295)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:280](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L280)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:286](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L286)

***

### merchant

> **merchant**: `string`

Defined in: [types/api.ts:283](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L283)

***

### price

> **price**: `string` \| `null`

Defined in: [types/api.ts:284](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L284)

***

### product

> **product**: `string`

Defined in: [types/api.ts:282](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L282)

***

### receipt

> **receipt**: [`Receipt`](Receipt.md)

Defined in: [types/api.ts:287](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L287)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:281](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L281)

***

### size

> **size**: `string`

Defined in: [types/api.ts:285](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L285)

***

### lines?

> `optional` **lines**: [`Line`](Line.md)[]

Defined in: [types/api.ts:294](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/types/api.ts#L294)

Per-receipt line list. The frontend never reads this — the cropped
image is built from `receipt` + `bbox`. Cache generator no longer
emits it (saves ~95% of payload). Kept optional only so older cached
responses still type-check during rollout.
