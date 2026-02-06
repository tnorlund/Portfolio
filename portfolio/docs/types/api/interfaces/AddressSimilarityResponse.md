[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / AddressSimilarityResponse

# Interface: AddressSimilarityResponse

Defined in: [types/api.ts:224](https://github.com/tnorlund/Portfolio/blob/141e9db5b00237898e7fe2a1c6b18874b1d831b0/portfolio/types/api.ts#L224)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:243](https://github.com/tnorlund/Portfolio/blob/141e9db5b00237898e7fe2a1c6b18874b1d831b0/portfolio/types/api.ts#L243)

***

### original

> **original**: `object`

Defined in: [types/api.ts:225](https://github.com/tnorlund/Portfolio/blob/141e9db5b00237898e7fe2a1c6b18874b1d831b0/portfolio/types/api.ts#L225)

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

Defined in: [types/api.ts:234](https://github.com/tnorlund/Portfolio/blob/141e9db5b00237898e7fe2a1c6b18874b1d831b0/portfolio/types/api.ts#L234)

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
