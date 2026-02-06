[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / WordSimilarityResponse

# Interface: WordSimilarityResponse

Defined in: [types/api.ts:228](https://github.com/tnorlund/Portfolio/blob/65d69d2b454e8071a0b7599b6fe31cf7715fb9b9/portfolio/types/api.ts#L228)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:247](https://github.com/tnorlund/Portfolio/blob/65d69d2b454e8071a0b7599b6fe31cf7715fb9b9/portfolio/types/api.ts#L247)

***

### original

> **original**: `object`

Defined in: [types/api.ts:230](https://github.com/tnorlund/Portfolio/blob/65d69d2b454e8071a0b7599b6fe31cf7715fb9b9/portfolio/types/api.ts#L230)

#### labels

> **labels**: [`ReceiptWordLabel`](ReceiptWordLabel.md)[]

#### lines

> **lines**: [`Line`](Line.md)[]

#### receipt

> **receipt**: [`Receipt`](Receipt.md)

#### target\_word

> **target\_word**: [`Word`](Word.md) \| `null`

#### words

> **words**: [`Word`](Word.md)[]

#### bbox?

> `optional` **bbox**: [`AddressBoundingBox`](AddressBoundingBox.md)

***

### query\_word

> **query\_word**: `string`

Defined in: [types/api.ts:229](https://github.com/tnorlund/Portfolio/blob/65d69d2b454e8071a0b7599b6fe31cf7715fb9b9/portfolio/types/api.ts#L229)

***

### similar

> **similar**: `object`[]

Defined in: [types/api.ts:238](https://github.com/tnorlund/Portfolio/blob/65d69d2b454e8071a0b7599b6fe31cf7715fb9b9/portfolio/types/api.ts#L238)

#### labels

> **labels**: [`ReceiptWordLabel`](ReceiptWordLabel.md)[]

#### lines

> **lines**: [`Line`](Line.md)[]

#### receipt

> **receipt**: [`Receipt`](Receipt.md)

#### similarity\_distance

> **similarity\_distance**: `number`

#### target\_word

> **target\_word**: [`Word`](Word.md)

#### words

> **words**: [`Word`](Word.md)[]

#### bbox?

> `optional` **bbox**: [`AddressBoundingBox`](AddressBoundingBox.md)
