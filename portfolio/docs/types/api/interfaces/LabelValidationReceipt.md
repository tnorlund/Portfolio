[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:663](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L663)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:674](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L674)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:668](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L668)

***

### height

> **height**: `number`

Defined in: [types/api.ts:681](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L681)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:664](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L664)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:669](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L669)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:666](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L666)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:665](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L665)

***

### width

> **width**: `number`

Defined in: [types/api.ts:680](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L680)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:667](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L667)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:676](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L676)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:679](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L679)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:677](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L677)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:678](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L678)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:675](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L675)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:670](https://github.com/tnorlund/Portfolio/blob/1579337b9a191179c56b0181a78f8ea28fb8d2f2/portfolio/types/api.ts#L670)
