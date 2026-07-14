[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationTier

# Interface: LabelValidationTier

Defined in: [types/api.ts:755](https://github.com/tnorlund/Portfolio/blob/fc8bbe6314c541717d3c6944bd6bc906502d9276/portfolio/types/api.ts#L755)

Validation tier results (ChromaDB or LLM) for the two-tier validation system.

## Properties

### decisions

> **decisions**: `object`

Defined in: [types/api.ts:759](https://github.com/tnorlund/Portfolio/blob/fc8bbe6314c541717d3c6944bd6bc906502d9276/portfolio/types/api.ts#L759)

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

Defined in: [types/api.ts:757](https://github.com/tnorlund/Portfolio/blob/fc8bbe6314c541717d3c6944bd6bc906502d9276/portfolio/types/api.ts#L757)

***

### tier

> **tier**: `"chroma"` \| `"llm"`

Defined in: [types/api.ts:756](https://github.com/tnorlund/Portfolio/blob/fc8bbe6314c541717d3c6944bd6bc906502d9276/portfolio/types/api.ts#L756)

***

### words\_count

> **words\_count**: `number`

Defined in: [types/api.ts:758](https://github.com/tnorlund/Portfolio/blob/fc8bbe6314c541717d3c6944bd6bc906502d9276/portfolio/types/api.ts#L758)
