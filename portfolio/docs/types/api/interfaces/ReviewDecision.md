[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReviewDecision

# Interface: ReviewDecision

Defined in: [types/api.ts:522](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L522)

## Properties

### consensus\_score

> **consensus\_score**: `number`

Defined in: [types/api.ts:525](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L525)

***

### evidence

> **evidence**: [`ReviewEvidence`](ReviewEvidence.md)[]

Defined in: [types/api.ts:537](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L537)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:523](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L523)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:527](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L527)

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

Defined in: [types/api.ts:538](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L538)

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

Defined in: [types/api.ts:524](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L524)

***

### similar\_word\_count

> **similar\_word\_count**: `number`

Defined in: [types/api.ts:526](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L526)
