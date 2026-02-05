[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:593](https://github.com/tnorlund/Portfolio/blob/255c590fea4833bc5f7e3b6e708a5b857b360228/portfolio/types/api.ts#L593)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:597](https://github.com/tnorlund/Portfolio/blob/255c590fea4833bc5f7e3b6e708a5b857b360228/portfolio/types/api.ts#L597)

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

Defined in: [types/api.ts:595](https://github.com/tnorlund/Portfolio/blob/255c590fea4833bc5f7e3b6e708a5b857b360228/portfolio/types/api.ts#L595)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:594](https://github.com/tnorlund/Portfolio/blob/255c590fea4833bc5f7e3b6e708a5b857b360228/portfolio/types/api.ts#L594)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:596](https://github.com/tnorlund/Portfolio/blob/255c590fea4833bc5f7e3b6e708a5b857b360228/portfolio/types/api.ts#L596)
