[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / WordSimilarityResponse

# Interface: WordSimilarityResponse

Defined in: [types/api.ts:277](https://github.com/tnorlund/Portfolio/blob/500e92ae90b42e2b028c3ad982d98fb922f7e06d/portfolio/types/api.ts#L277)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:296](https://github.com/tnorlund/Portfolio/blob/500e92ae90b42e2b028c3ad982d98fb922f7e06d/portfolio/types/api.ts#L296)

***

### original

> **original**: `object`

Defined in: [types/api.ts:279](https://github.com/tnorlund/Portfolio/blob/500e92ae90b42e2b028c3ad982d98fb922f7e06d/portfolio/types/api.ts#L279)

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

Defined in: [types/api.ts:278](https://github.com/tnorlund/Portfolio/blob/500e92ae90b42e2b028c3ad982d98fb922f7e06d/portfolio/types/api.ts#L278)

***

### similar

> **similar**: `object`[]

Defined in: [types/api.ts:287](https://github.com/tnorlund/Portfolio/blob/500e92ae90b42e2b028c3ad982d98fb922f7e06d/portfolio/types/api.ts#L287)

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
