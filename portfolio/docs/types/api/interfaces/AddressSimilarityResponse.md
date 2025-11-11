[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / AddressSimilarityResponse

# Interface: AddressSimilarityResponse

Defined in: [types/api.ts:177](https://github.com/tnorlund/Portfolio/blob/ca6cf51fe1795dd90720ffc648ab54b81ec15592/portfolio/types/api.ts#L177)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:196](https://github.com/tnorlund/Portfolio/blob/ca6cf51fe1795dd90720ffc648ab54b81ec15592/portfolio/types/api.ts#L196)

***

### original

> **original**: `object`

Defined in: [types/api.ts:178](https://github.com/tnorlund/Portfolio/blob/ca6cf51fe1795dd90720ffc648ab54b81ec15592/portfolio/types/api.ts#L178)

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

Defined in: [types/api.ts:187](https://github.com/tnorlund/Portfolio/blob/ca6cf51fe1795dd90720ffc648ab54b81ec15592/portfolio/types/api.ts#L187)

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
