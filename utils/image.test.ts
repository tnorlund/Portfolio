import { getBestImageUrl, type FormatSupport } from "./image";

describe("image utilities", () => {
  describe("getBestImageUrl", () => {
    const mockImage = {
      image_id: "1",
      width: 100,
      height: 100,
      timestamp_added: "2023-01-01",
      raw_s3_bucket: "bucket",
      raw_s3_key: "raw.jpg",
      sha256: "abc123",
      cdn_s3_bucket: "cdn-bucket",
      cdn_s3_key: "test.jpg",
      cdn_webp_s3_key: "test.webp",
      cdn_avif_s3_key: "test.avif",
      image_type: "jpg" as const,
    };

    test("prefers AVIF when supported and available", () => {
      const url = getBestImageUrl(mockImage, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(url).toContain("test.avif");
      expect(url).toMatch(
        /^https:\/\/(www|dev)\.tylernorlund\.com\/test\.avif$/
      );
    });

    test("falls back to WebP when AVIF not supported", () => {
      const url = getBestImageUrl(mockImage, {
        supportsAVIF: false,
        supportsWebP: true,
      });
      expect(url).toContain("test.webp");
      expect(url).toMatch(
        /^https:\/\/(www|dev)\.tylernorlund\.com\/test\.webp$/
      );
    });

    test("falls back to original when no modern formats supported", () => {
      const url = getBestImageUrl(mockImage, {
        supportsAVIF: false,
        supportsWebP: false,
      });
      expect(url).toContain("test.jpg");
      expect(url).toMatch(
        /^https:\/\/(www|dev)\.tylernorlund\.com\/test\.jpg$/
      );
    });

    test("falls back when AVIF key is missing", () => {
      const imageWithoutAVIF = { ...mockImage, cdn_avif_s3_key: undefined };
      const url = getBestImageUrl(imageWithoutAVIF, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(url).toContain("test.webp");
      expect(url).toMatch(
        /^https:\/\/(www|dev)\.tylernorlund\.com\/test\.webp$/
      );
    });

    test("falls back when WebP key is missing", () => {
      const imageWithoutWebP = { ...mockImage, cdn_webp_s3_key: undefined };
      const url = getBestImageUrl(imageWithoutWebP, {
        supportsAVIF: false,
        supportsWebP: true,
      });
      expect(url).toContain("test.jpg");
      expect(url).toMatch(
        /^https:\/\/(www|dev)\.tylernorlund\.com\/test\.jpg$/
      );
    });

    test("handles missing both modern format keys", () => {
      const imageWithoutModern = {
        ...mockImage,
        cdn_webp_s3_key: undefined,
        cdn_avif_s3_key: undefined,
      };
      const url = getBestImageUrl(imageWithoutModern, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(url).toContain("test.jpg");
      expect(url).toMatch(
        /^https:\/\/(www|dev)\.tylernorlund\.com\/test\.jpg$/
      );
    });

    test("handles format support combinations correctly", () => {
      // Test all format support combinations
      const formatCombinations: Array<{
        support: FormatSupport;
        expectedFile: string;
      }> = [
        {
          support: { supportsAVIF: true, supportsWebP: true },
          expectedFile: "test.avif",
        },
        {
          support: { supportsAVIF: true, supportsWebP: false },
          expectedFile: "test.avif",
        },
        {
          support: { supportsAVIF: false, supportsWebP: true },
          expectedFile: "test.webp",
        },
        {
          support: { supportsAVIF: false, supportsWebP: false },
          expectedFile: "test.jpg",
        },
      ];

      formatCombinations.forEach(({ support, expectedFile }) => {
        const url = getBestImageUrl(mockImage, support);
        expect(url).toContain(expectedFile);
        expect(url).toMatch(
          new RegExp(`^https://(www|dev)\\.tylernorlund\\.com/${expectedFile}$`)
        );
      });
    });

    test("uses correct base URL structure", () => {
      const url = getBestImageUrl(mockImage, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(url).toMatch(/^https:\/\/(www|dev)\.tylernorlund\.com\//);
    });

    test("handles undefined optional keys gracefully", () => {
      const imageWithSomeUndefined = {
        ...mockImage,
        cdn_webp_s3_key: undefined,
        cdn_avif_s3_key: "test.avif",
      };

      // Should still prefer AVIF when available
      const avifUrl = getBestImageUrl(imageWithSomeUndefined, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(avifUrl).toContain("test.avif");

      // Should fall back to original when AVIF not supported and WebP missing
      const fallbackUrl = getBestImageUrl(imageWithSomeUndefined, {
        supportsAVIF: false,
        supportsWebP: true,
      });
      expect(fallbackUrl).toContain("test.jpg");
    });

    test("handles empty string keys", () => {
      const imageWithEmptyKeys = {
        ...mockImage,
        cdn_webp_s3_key: "",
        cdn_avif_s3_key: "",
      };

      const url = getBestImageUrl(imageWithEmptyKeys, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(url).toContain("test.jpg"); // Should fall back to original
    });

    test("validates format preference order", () => {
      // AVIF should always be preferred over WebP when both are available and supported
      const url1 = getBestImageUrl(mockImage, {
        supportsAVIF: true,
        supportsWebP: true,
      });
      expect(url1).toContain("test.avif");
      expect(url1).not.toContain("test.webp");

      // WebP should be preferred over original when AVIF not supported
      const url2 = getBestImageUrl(mockImage, {
        supportsAVIF: false,
        supportsWebP: true,
      });
      expect(url2).toContain("test.webp");
      expect(url2).not.toContain("test.jpg");
      expect(url2).not.toContain("test.avif");

      // Original should be used when no modern formats supported
      const url3 = getBestImageUrl(mockImage, {
        supportsAVIF: false,
        supportsWebP: false,
      });
      expect(url3).toContain("test.jpg");
      expect(url3).not.toContain("test.webp");
      expect(url3).not.toContain("test.avif");
    });
  });
});
