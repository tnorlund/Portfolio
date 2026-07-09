[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReviewDecision

# Interface: ReviewDecision

Defined in: [types/api.ts:638](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L638)

## Properties

### consensus\_score

> **consensus\_score**: `number`

Defined in: [types/api.ts:641](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L641)

***

### evidence

> **evidence**: [`ReviewEvidence`](ReviewEvidence.md)[]

Defined in: [types/api.ts:653](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L653)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:639](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L639)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:643](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L643)

#### current\_label

> **current\_label**: `string` \| `null`

#### line\_id

> **line\_id**: `number`

#### reasoning

> **reasoning**: `string`

#### suggested\_label

> **suggested\_label**: `string`

#### suggested\_status

> **suggested\_status**: `string`

#### type

> **type**: `string`

#### word\_id

> **word\_id**: `number`

#### word\_text

> **word\_text**: `string`

***

### llm\_review

> **llm\_review**: `object`

Defined in: [types/api.ts:654](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L654)

#### confidence

> **confidence**: `"high"` \| `"medium"` \| `"low"`

#### decision

> **decision**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"`

#### reasoning

> **reasoning**: `string`

#### suggested\_label

> **suggested\_label**: `string` \| `null`

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:640](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L640)

***

### similar\_word\_count

> **similar\_word\_count**: `number`

Defined in: [types/api.ts:642](https://github.com/tnorlund/Portfolio/blob/ab5b6be34144fcfa53110251b10db59dd6bb2e47/portfolio/types/api.ts#L642)
