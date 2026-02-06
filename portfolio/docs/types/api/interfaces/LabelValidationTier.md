[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:611](https://github.com/tnorlund/Portfolio/blob/9946771be3f39075fa05818b03cfad131b86b1aa/portfolio/types/api.ts#L611)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:615](https://github.com/tnorlund/Portfolio/blob/9946771be3f39075fa05818b03cfad131b86b1aa/portfolio/types/api.ts#L615)

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

Defined in: [types/api.ts:613](https://github.com/tnorlund/Portfolio/blob/9946771be3f39075fa05818b03cfad131b86b1aa/portfolio/types/api.ts#L613)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:612](https://github.com/tnorlund/Portfolio/blob/9946771be3f39075fa05818b03cfad131b86b1aa/portfolio/types/api.ts#L612)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/9946771be3f39075fa05818b03cfad131b86b1aa/portfolio/types/api.ts#L614)
