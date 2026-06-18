/**
 * Base URL for receipt image assets (CDN), resolved at build time.
 *
 * Images must come from the SAME environment as the data: the dev site shows
 * dev data (dev API) whose image variants live on the dev CDN, and prod shows
 * prod data on the prod CDN. We bake `NEXT_PUBLIC_CDN_URL` per deployment
 * (e.g. `https://dev.tylernorlund.com` for the dev build) so the site loads
 * images from the right CDN. The `NODE_ENV` branch is only a local dev-server
 * fallback when the env var isn't set.
 */
export const getCdnBaseUrl = (): string =>
  process.env.NEXT_PUBLIC_CDN_URL ||
  (process.env.NODE_ENV === "development"
    ? "https://dev.tylernorlund.com"
    : "https://www.tylernorlund.com");
