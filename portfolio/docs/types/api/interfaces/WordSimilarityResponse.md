[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / WordSimilarityResponse

# Interface: WordSimilarityResponse

Defined in: [types/api.ts:246](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L246)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:265](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L265)

***

### original

> **original**: `object`

Defined in: [types/api.ts:248](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L248)

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

Defined in: [types/api.ts:247](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L247)

***

### similar

> **similar**: `object`[]

Defined in: [types/api.ts:256](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L256)

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
