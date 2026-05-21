[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:657](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L657)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:668](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L668)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:662](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L662)

***

### height

> **height**: `number`

Defined in: [types/api.ts:675](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L675)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:658](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L658)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:663](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L663)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:660](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L660)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:659](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L659)

***

### width

> **width**: `number`

Defined in: [types/api.ts:674](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L674)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:661](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L661)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:670](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L670)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:673](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L673)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:671](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L671)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:672](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L672)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:669](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L669)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:664](https://github.com/tnorlund/Portfolio/blob/428639f1b31b2db3211b6e31826adfe2edfb77f7/portfolio/types/api.ts#L664)
