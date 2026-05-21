[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:641](https://github.com/tnorlund/Portfolio/blob/5cdc00c960c55c967f5ffdec37d005d35c2ea5a6/portfolio/types/api.ts#L641)

Validation tier results (ChromaDB or LLM) for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:645](https://github.com/tnorlund/Portfolio/blob/5cdc00c960c55c967f5ffdec37d005d35c2ea5a6/portfolio/types/api.ts#L645)

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

Defined in: [types/api.ts:643](https://github.com/tnorlund/Portfolio/blob/5cdc00c960c55c967f5ffdec37d005d35c2ea5a6/portfolio/types/api.ts#L643)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:642](https://github.com/tnorlund/Portfolio/blob/5cdc00c960c55c967f5ffdec37d005d35c2ea5a6/portfolio/types/api.ts#L642)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:644](https://github.com/tnorlund/Portfolio/blob/5cdc00c960c55c967f5ffdec37d005d35c2ea5a6/portfolio/types/api.ts#L644)
