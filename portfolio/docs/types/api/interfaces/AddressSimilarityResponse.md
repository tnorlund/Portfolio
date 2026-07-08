[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / AddressSimilarityResponse

# Interface: AddressSimilarityResponse

Defined in: [types/api.ts:255](https://github.com/tnorlund/Portfolio/blob/205a0a8450990413b43fae59d81365994a8a4c4d/portfolio/types/api.ts#L255)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:274](https://github.com/tnorlund/Portfolio/blob/205a0a8450990413b43fae59d81365994a8a4c4d/portfolio/types/api.ts#L274)

***

### original

> **original**: `object`

Defined in: [types/api.ts:256](https://github.com/tnorlund/Portfolio/blob/205a0a8450990413b43fae59d81365994a8a4c4d/portfolio/types/api.ts#L256)

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

Defined in: [types/api.ts:265](https://github.com/tnorlund/Portfolio/blob/205a0a8450990413b43fae59d81365994a8a4c4d/portfolio/types/api.ts#L265)

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
