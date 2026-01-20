[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationReceipt

# Interface: LabelValidationReceipt

Defined in: [types/api.ts:602](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L602)

Receipt with label validation results.
Contains words with their validation decisions and CDN image keys.

## Properties

### cdn\_s3\_key

> **cdn\_s3\_key**: `string`

Defined in: [types/api.ts:613](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L613)

***

### chroma

> **chroma**: [`LabelValidationTier`](LabelValidationTier.md)

Defined in: [types/api.ts:607](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L607)

***

### height

> **height**: `number`

Defined in: [types/api.ts:620](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L620)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:603](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L603)

***

### llm

> **llm**: [`LabelValidationTier`](LabelValidationTier.md) \| `null`

Defined in: [types/api.ts:608](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L608)

***

### merchant\_name

> **merchant\_name**: `string` \| `null`

Defined in: [types/api.ts:605](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L605)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:604](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L604)

***

### width

> **width**: `number`

Defined in: [types/api.ts:619](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L619)

***

### words

> **words**: [`LabelValidationWord`](LabelValidationWord.md)[]

Defined in: [types/api.ts:606](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L606)

***

### cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:615](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L615)

***

### cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

Defined in: [types/api.ts:618](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L618)

***

### cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

Defined in: [types/api.ts:616](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L616)

***

### cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:617](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L617)

***

### cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L614)

***

### step\_timings?

> `optional` **step\_timings**: `Record`\<`string`, \{ `duration_ms`: `number`; `duration_seconds`: `number`; \}\>

Defined in: [types/api.ts:609](https://github.com/tnorlund/Portfolio/blob/cbdeee77317af288175ce6ab1eb946c92002b9e8/portfolio/types/api.ts#L609)
