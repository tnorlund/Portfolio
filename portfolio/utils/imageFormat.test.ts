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

  test("thumbnail request cascades to medium when no thumbnail/small keys exist", () => {
    // Matches the real prod API shape: only cdn_* (full) + cdn_medium_* exist.
    // A 'thumbnail' request must NOT silently fall back to the full-size asset.
    const image = {
      cdn_s3_key: "a.jpg",
      cdn_webp_s3_key: "a.webp",
      cdn_avif_s3_key: "a.avif",
      cdn_medium_s3_key: "a_med.jpg",
      cdn_medium_webp_s3_key: "a_med.webp",
      cdn_medium_avif_s3_key: "a_med.avif",
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "production" };

    const url = getBestImageUrl(
      image,
      { supportsAVIF: true, supportsWebP: true },
      "thumbnail"
    );
    expect(url).toBe("https://www.tylernorlund.com/a_med.avif");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });

  test("prefers thumbnail JPEG over medium AVIF when caller asked for thumbnail", () => {
    // Codex P2 regression guard: a 'thumbnail' request must NOT skip a
    // tiny thumbnail JPEG just because a larger medium AVIF exists.
    // Bandwidth at the smallest tier beats codec efficiency.
    const image = {
      cdn_s3_key: "a.jpg",
      cdn_avif_s3_key: "a.avif",
      cdn_medium_avif_s3_key: "a_med.avif",
      cdn_thumbnail_s3_key: "a_thumb.jpg",
      // Note: no thumbnail AVIF or WebP available
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "production" };

    const url = getBestImageUrl(
      image,
      { supportsAVIF: true, supportsWebP: true },
      "thumbnail"
    );
    expect(url).toBe("https://www.tylernorlund.com/a_thumb.jpg");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });

  test("full request returns full-size even when medium variants exist", () => {
    // Full-size request must not "borrow" the medium variant just because
    // it exists at a different size tier.
    const image = {
      cdn_s3_key: "a.jpg",
      cdn_avif_s3_key: "a.avif",
      cdn_medium_s3_key: "a_med.jpg",
      cdn_medium_avif_s3_key: "a_med.avif",
    };

    const originalEnv = process.env.NODE_ENV;
    process.env = { ...process.env, NODE_ENV: "production" };

    const url = getBestImageUrl(
      image,
      { supportsAVIF: true, supportsWebP: false },
      "full"
    );
    expect(url).toBe("https://www.tylernorlund.com/a.avif");

    process.env = { ...process.env, NODE_ENV: originalEnv };
  });
});
