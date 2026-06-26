[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / MilkReceiptData

# Interface: MilkReceiptData

Defined in: [types/api.ts:310](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L310)

## Properties

### bbox

> **bbox**: [`AddressBoundingBox`](AddressBoundingBox.md) \| `null`

Defined in: [types/api.ts:326](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L326)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:311](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L311)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:317](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L317)

***

### merchant

> **merchant**: `string`

Defined in: [types/api.ts:314](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L314)

***

### price

> **price**: `string` \| `null`

Defined in: [types/api.ts:315](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L315)

***

### product

> **product**: `string`

Defined in: [types/api.ts:313](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L313)

***

### receipt

> **receipt**: [`Receipt`](Receipt.md)

Defined in: [types/api.ts:318](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L318)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:312](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L312)

***

### size

> **size**: `string`

Defined in: [types/api.ts:316](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L316)

***

### lines?

> `optional` **lines**: [`Line`](Line.md)[]

Defined in: [types/api.ts:325](https://github.com/tnorlund/Portfolio/blob/e7dadefc7eaa49f9e79998fa6753b62047b88a70/portfolio/types/api.ts#L325)

Per-receipt line list. The frontend never reads this — the cropped
image is built from `receipt` + `bbox`. Cache generator no longer
emits it (saves ~95% of payload). Kept optional only so older cached
responses still type-check during rollout.
