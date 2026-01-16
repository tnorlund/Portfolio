[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / AddressSimilarityResponse

# Interface: AddressSimilarityResponse

Defined in: [types/api.ts:206](https://github.com/tnorlund/Portfolio/blob/4f500c421028dc100bad3efa0fa9a9007259e336/portfolio/types/api.ts#L206)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:225](https://github.com/tnorlund/Portfolio/blob/4f500c421028dc100bad3efa0fa9a9007259e336/portfolio/types/api.ts#L225)

***

### original

> **original**: `object`

Defined in: [types/api.ts:207](https://github.com/tnorlund/Portfolio/blob/4f500c421028dc100bad3efa0fa9a9007259e336/portfolio/types/api.ts#L207)

#### labels

> **labels**: [`ReceiptWordLabel`](ReceiptWordLabel.md)[]

#### lines

> **lines**: [`Line`](Line.md)[]

#### receipt

> **receipt**: [`Receipt`](Receipt.md)

#### words

> **words**: [`Word`](Word.md)[]

#### address\_text?

> `optional` **address\_text**: `string`

#### bbox?

> `optional` **bbox**: [`AddressBoundingBox`](AddressBoundingBox.md)

#### selected\_group?

> `optional` **selected\_group**: `number`[]

***

### similar

> **similar**: `object`[]

Defined in: [types/api.ts:216](https://github.com/tnorlund/Portfolio/blob/4f500c421028dc100bad3efa0fa9a9007259e336/portfolio/types/api.ts#L216)

#### labels

> **labels**: [`ReceiptWordLabel`](ReceiptWordLabel.md)[]

#### lines

> **lines**: [`Line`](Line.md)[]

#### receipt

> **receipt**: [`Receipt`](Receipt.md)

#### similarity\_distance

> **similarity\_distance**: `number`

#### words

> **words**: [`Word`](Word.md)[]

#### address\_text?

> `optional` **address\_text**: `string`

#### bbox?

> `optional` **bbox**: [`AddressBoundingBox`](AddressBoundingBox.md)
