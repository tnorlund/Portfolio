[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/testing](../README.md) / profileFunction

# Function: profileFunction()

> **profileFunction**\<`T`\>(`name`, `fn`): `Promise`\<\{ `profile`: `PerformanceProfile`; `result`: `T`; \}\>

Defined in: [utils/performance/testing.ts:137](https://github.com/tnorlund/Portfolio/blob/187460003383ab25549f0023f303010e8b254201/portfolio/utils/performance/testing.ts#L137)

Profile a function and get detailed performance breakdown

## Type Parameters

### T

`T`

## Parameters

### name

`string`

### fn

() => `T` \| `Promise`\<`T`\>

## Returns

`Promise`\<\{ `profile`: `PerformanceProfile`; `result`: `T`; \}\>
