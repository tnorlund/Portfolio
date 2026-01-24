[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:609](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L609)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:620](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L620)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L614)

***

### height

> **height**: `number`

Defined in: [types/api.ts:627](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L627)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:610](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L610)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:615](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L615)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:612](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L612)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:611](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L611)

***

### width

> **width**: `number`

Defined in: [types/api.ts:626](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L626)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:613](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L613)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:622](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L622)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:625](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L625)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:623](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L623)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:624](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L624)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:621](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L621)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:616](https://github.com/tnorlund/Portfolio/blob/4478a9d860791e37f646715511829f02dce05f2d/portfolio/types/api.ts#L616)
