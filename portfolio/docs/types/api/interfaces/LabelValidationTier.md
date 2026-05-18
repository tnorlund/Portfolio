[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:645](https://github.com/tnorlund/Portfolio/blob/7dbeb29a122f0d393ef39a6ff949fe905a8e7089/portfolio/types/api.ts#L645)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:649](https://github.com/tnorlund/Portfolio/blob/7dbeb29a122f0d393ef39a6ff949fe905a8e7089/portfolio/types/api.ts#L649)

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

Defined in: [types/api.ts:647](https://github.com/tnorlund/Portfolio/blob/7dbeb29a122f0d393ef39a6ff949fe905a8e7089/portfolio/types/api.ts#L647)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:646](https://github.com/tnorlund/Portfolio/blob/7dbeb29a122f0d393ef39a6ff949fe905a8e7089/portfolio/types/api.ts#L646)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:648](https://github.com/tnorlund/Portfolio/blob/7dbeb29a122f0d393ef39a6ff949fe905a8e7089/portfolio/types/api.ts#L648)
