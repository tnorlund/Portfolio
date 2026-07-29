[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/cdnBase](../README.md) / getCdnBaseUrl

# Function: getCdnBaseUrl()

> **getCdnBaseUrl**(): `string`

Defined in: [utils/cdnBase.ts:11](https://github.com/tnorlund/Portfolio/blob/6a6e362ebf01d3c36b05b851c068110373deaf8f/portfolio/utils/cdnBase.ts#L11)

Base URL for receipt image assets (CDN), resolved at build time.

Images must come from the SAME environment as the data: the dev site shows
dev data (dev API) whose image variants live on the dev CDN, and prod shows
prod data on the prod CDN. We bake `NEXT_PUBLIC_CDN_URL` per deployment
(e.g. `https://dev.tylernorlund.com` for the dev build) so the site loads
images from the right CDN. The `NODE_ENV` branch is only a local dev-server
fallback when the env var isn't set.

## Returns

`string`
