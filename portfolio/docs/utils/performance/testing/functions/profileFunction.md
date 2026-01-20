[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/testing](../README.md) / profileFunction

# Function: profileFunction()

> **profileFunction**\<`T`\>(`name`, `fn`): `Promise`\<\{ `profile`: [`PerformanceProfile`](../interfaces/PerformanceProfile.md); `result`: `T`; \}\>

Defined in: [utils/performance/testing.ts:137](https://github.com/tnorlund/Portfolio/blob/747939f0e3b4a359ba380ac2239535fab933547e/portfolio/utils/performance/testing.ts#L137)

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
