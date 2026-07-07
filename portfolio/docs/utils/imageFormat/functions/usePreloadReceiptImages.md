[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/imageFormat](../README.md) / usePreloadReceiptImages

# Function: usePreloadReceiptImages()

> **usePreloadReceiptImages**(`images`, `formatSupport`): `void`

Defined in: [utils/imageFormat.ts:211](https://github.com/tnorlund/Portfolio/blob/03e836d9f735112e2d8c90c57678e1792095d3df/portfolio/utils/imageFormat.ts#L211)

Preload full-size, medium, and thumbnail images for all receipts so they're
browser-cached before flying-receipt animations need them.
Uses a ref-based set to avoid re-fetching already-preloaded URLs,
which keeps it efficient for components that continuously append receipts.

## Parameters

### images

[`ImageFormats`](../interfaces/ImageFormats.md)[]

### formatSupport

[`FormatSupport`](../interfaces/FormatSupport.md) | `null`

## Returns

`void`
