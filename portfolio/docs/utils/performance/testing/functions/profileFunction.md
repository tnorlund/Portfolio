[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/testing](../README.md) / profileFunction

# Function: profileFunction()

> **profileFunction**\<`T`\>(`name`, `fn`): `Promise`\<\{ `profile`: [`PerformanceProfile`](../interfaces/PerformanceProfile.md); `result`: `T`; \}\>

Defined in: [utils/performance/testing.ts:137](https://github.com/tnorlund/Portfolio/blob/f7d39c89f1fe991f8fa7e4e4622c8bd678b1cfb2/portfolio/utils/performance/testing.ts#L137)

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

`Promise`\<\{ `profile`: [`PerformanceProfile`](../interfaces/PerformanceProfile.md); `result`: `T`; \}\>
