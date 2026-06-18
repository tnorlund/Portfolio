[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:740](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L740)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:751](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L751)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:745](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L745)

***

### height

> **height**: `number`

Defined in: [types/api.ts:758](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L758)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:741](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L741)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:746](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L746)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:743](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L743)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:742](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L742)

***

### width

> **width**: `number`

Defined in: [types/api.ts:757](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L757)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:744](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L744)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:753](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L753)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:756](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L756)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:754](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L754)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:755](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L755)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:752](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L752)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:747](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L747)
