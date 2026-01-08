[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / AddressSimilarityResponse

# Interface: AddressSimilarityResponse

Defined in: [types/api.ts:201](https://github.com/tnorlund/Portfolio/blob/3ac4bb851385dce4aca7beffa5688827d2ad7dd4/portfolio/types/api.ts#L201)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:220](https://github.com/tnorlund/Portfolio/blob/3ac4bb851385dce4aca7beffa5688827d2ad7dd4/portfolio/types/api.ts#L220)

***

### original

> **original**: `object`

Defined in: [types/api.ts:202](https://github.com/tnorlund/Portfolio/blob/3ac4bb851385dce4aca7beffa5688827d2ad7dd4/portfolio/types/api.ts#L202)

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

Defined in: [types/api.ts:211](https://github.com/tnorlund/Portfolio/blob/3ac4bb851385dce4aca7beffa5688827d2ad7dd4/portfolio/types/api.ts#L211)

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
