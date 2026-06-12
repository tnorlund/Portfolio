[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:647](https://github.com/tnorlund/Portfolio/blob/1903cdf27107e96b5429f4b5e1c96a864291c888/portfolio/types/api.ts#L647)

Validation tier results (ChromaDB or LLM) for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:651](https://github.com/tnorlund/Portfolio/blob/1903cdf27107e96b5429f4b5e1c96a864291c888/portfolio/types/api.ts#L651)

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

Defined in: [types/api.ts:649](https://github.com/tnorlund/Portfolio/blob/1903cdf27107e96b5429f4b5e1c96a864291c888/portfolio/types/api.ts#L649)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:648](https://github.com/tnorlund/Portfolio/blob/1903cdf27107e96b5429f4b5e1c96a864291c888/portfolio/types/api.ts#L648)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:650](https://github.com/tnorlund/Portfolio/blob/1903cdf27107e96b5429f4b5e1c96a864291c888/portfolio/types/api.ts#L650)
