[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:591](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L591)

Validation tier results (ChromaDB or LLM).
Similar to LabelEvaluatorEvaluation but for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:595](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L595)

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

Defined in: [types/api.ts:593](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L593)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:592](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L592)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:594](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L594)
