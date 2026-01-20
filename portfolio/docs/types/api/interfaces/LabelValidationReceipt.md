[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:607](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L607)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:618](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L618)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:612](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L612)

***

### height

> **height**: `number`

Defined in: [types/api.ts:625](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L625)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:608](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L608)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:613](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L613)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:610](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L610)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:609](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L609)

***

### width

> **width**: `number`

Defined in: [types/api.ts:624](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L624)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:611](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L611)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:620](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L620)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:623](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L623)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:621](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L621)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:622](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L622)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:619](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L619)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L614)
