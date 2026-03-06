[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:661](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L661)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:672](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L672)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:666](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L666)

***

### height

> **height**: `number`

Defined in: [types/api.ts:679](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L679)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:662](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L662)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:667](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L667)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:664](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L664)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:663](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L663)

***

### width

> **width**: `number`

Defined in: [types/api.ts:678](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L678)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:665](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L665)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:674](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L674)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:677](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L677)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:675](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L675)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:676](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L676)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:673](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L673)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:668](https://github.com/tnorlund/Portfolio/blob/81efd0e13f71086e1e6c551128ed743937ee9d62/portfolio/types/api.ts#L668)
