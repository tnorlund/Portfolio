[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:771](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L771)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:782](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L782)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:776](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L776)

***

### height

> **height**: `number`

Defined in: [types/api.ts:789](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L789)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:772](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L772)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:777](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L777)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:774](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L774)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:773](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L773)

***

### width

> **width**: `number`

Defined in: [types/api.ts:788](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L788)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:775](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L775)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:784](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L784)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:787](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L787)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:785](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L785)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:786](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L786)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:783](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L783)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:778](https://github.com/tnorlund/Portfolio/blob/19424371532760002e1c782927cb247154e1e975/portfolio/types/api.ts#L778)
