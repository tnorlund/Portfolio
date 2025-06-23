import { getBestImageUrl } from "./imageFormat";

describe("getBestImageUrl", () => {
  test("prefers avif then webp then fallback in development", () => {
    const image = {
      cdn_s3_key: "a.png",
      cdn_webp_s3_key: "a.webp",
      cdn_avif_s3_key: "a.avif",
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "development" };

    const avif = getBestImageUrl(image, {
      supportsAVIF: true,
      supportsWebP: true,
    });
    expect(avif).toBe("https://dev.tylernorlund.com/a.avif");

    const webp = getBestImageUrl(image, {
      supportsAVIF: false,
      supportsWebP: true,
    });
    expect(webp).toBe("https://dev.tylernorlund.com/a.webp");

    const fallback = getBestImageUrl(image, {
      supportsAVIF: false,
      supportsWebP: false,
    });
    expect(fallback).toBe("https://dev.tylernorlund.com/a.png");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });

  test("uses production URL in production", () => {
    const image = {
      cdn_s3_key: "a.png",
      cdn_webp_s3_key: "a.webp",
      cdn_avif_s3_key: "a.avif",
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "production" };

    const url = getBestImageUrl(image, {
      supportsAVIF: true,
      supportsWebP: true,
    });
    expect(url).toBe("https://www.tylernorlund.com/a.avif");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });

  test("handles missing format keys gracefully", () => {
    const image = {
      cdn_s3_key: "a.png",
      cdn_webp_s3_key: "a.webp",
      // no avif key
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "development" };

    const url = getBestImageUrl(image, {
      supportsAVIF: true,
      supportsWebP: true,
    });
    expect(url).toBe("https://dev.tylernorlund.com/a.webp");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });

  test("handles missing WebP key gracefully", () => {
    const image = {
      cdn_s3_key: "a.png",
      // no webp or avif keys
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "development" };

    const url = getBestImageUrl(image, {
      supportsAVIF: false,
      supportsWebP: true,
    });
    expect(url).toBe("https://dev.tylernorlund.com/a.png");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });

  test("handles missing both modern format keys", () => {
    const image = {
      cdn_s3_key: "a.png",
      // no webp or avif keys
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "production" };

    const url = getBestImageUrl(image, {
      supportsAVIF: true,
      supportsWebP: true,
    });
    expect(url).toBe("https://www.tylernorlund.com/a.png");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });
});
