[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:724](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L724)

Validation tier results (ChromaDB or LLM) for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:728](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L728)

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

Defined in: [types/api.ts:726](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L726)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:725](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L725)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:727](https://github.com/tnorlund/Portfolio/blob/79f97f95af9cab55714897e99ffdffa4dc035c94/portfolio/types/api.ts#L727)
