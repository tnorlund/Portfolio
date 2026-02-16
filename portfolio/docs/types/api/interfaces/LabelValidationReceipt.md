[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:659](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L659)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:670](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L670)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:664](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L664)

***

### height

> **height**: `number`

Defined in: [types/api.ts:677](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L677)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:660](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L660)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:665](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L665)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:662](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L662)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:661](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L661)

***

### width

> **width**: `number`

Defined in: [types/api.ts:676](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L676)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:663](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L663)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:672](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L672)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:675](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L675)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:673](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L673)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:674](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L674)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:671](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L671)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:666](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L666)
