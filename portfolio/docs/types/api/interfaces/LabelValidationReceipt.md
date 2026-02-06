[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:627](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L627)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:638](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L638)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:632](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L632)

***

### height

> **height**: `number`

Defined in: [types/api.ts:645](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L645)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:628](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L628)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:633](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L633)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:630](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L630)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:629](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L629)

***

### width

> **width**: `number`

Defined in: [types/api.ts:644](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L644)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:631](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L631)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:640](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L640)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:643](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L643)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:641](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L641)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:642](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L642)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:639](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L639)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:634](https://github.com/tnorlund/Portfolio/blob/35d2e8c4c7d3f83348db0a236563501be823ec0a/portfolio/types/api.ts#L634)
