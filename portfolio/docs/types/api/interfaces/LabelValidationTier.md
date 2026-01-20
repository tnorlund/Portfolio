[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:586](https://github.com/tnorlund/Portfolio/blob/f6f65dbed5ba502375f38f46542b80c5fca2980b/portfolio/types/api.ts#L586)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:590](https://github.com/tnorlund/Portfolio/blob/f6f65dbed5ba502375f38f46542b80c5fca2980b/portfolio/types/api.ts#L590)

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

Defined in: [types/api.ts:588](https://github.com/tnorlund/Portfolio/blob/f6f65dbed5ba502375f38f46542b80c5fca2980b/portfolio/types/api.ts#L588)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:587](https://github.com/tnorlund/Portfolio/blob/f6f65dbed5ba502375f38f46542b80c5fca2980b/portfolio/types/api.ts#L587)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:589](https://github.com/tnorlund/Portfolio/blob/f6f65dbed5ba502375f38f46542b80c5fca2980b/portfolio/types/api.ts#L589)
