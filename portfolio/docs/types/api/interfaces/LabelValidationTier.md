[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:643](https://github.com/tnorlund/Portfolio/blob/c4d7207ff2b42f477e2d98b3767374ea806361e6/portfolio/types/api.ts#L643)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:647](https://github.com/tnorlund/Portfolio/blob/c4d7207ff2b42f477e2d98b3767374ea806361e6/portfolio/types/api.ts#L647)

#### INVALID

> **INVALID**: `number`

#### NEEDS\_REVIEW

> **NEEDS\_REVIEW**: `number`

#### VALID

> **VALID**: `number`

#### UNKNOWN?

> `optional` **UNKNOWN**: `number`

***

### duration\_seconds

> **duration\_seconds**: `number`

Defined in: [types/api.ts:645](https://github.com/tnorlund/Portfolio/blob/c4d7207ff2b42f477e2d98b3767374ea806361e6/portfolio/types/api.ts#L645)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:644](https://github.com/tnorlund/Portfolio/blob/c4d7207ff2b42f477e2d98b3767374ea806361e6/portfolio/types/api.ts#L644)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:646](https://github.com/tnorlund/Portfolio/blob/c4d7207ff2b42f477e2d98b3767374ea806361e6/portfolio/types/api.ts#L646)
