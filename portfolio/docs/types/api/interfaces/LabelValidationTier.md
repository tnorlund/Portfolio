[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:611](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L611)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:615](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L615)

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

Defined in: [types/api.ts:613](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L613)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:612](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L612)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L614)
