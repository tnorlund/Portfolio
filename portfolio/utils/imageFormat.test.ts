import { getBestImageUrl } from './imageFormat';

const originalEnv = process.env.NODE_ENV;

afterEach(() => {
  process.env.NODE_ENV = originalEnv;
});

describe('getBestImageUrl', () => {
  test('prefers avif then webp then fallback', () => {
    const image = {
      cdn_s3_key: 'a.png',
      cdn_webp_s3_key: 'a.webp',
      cdn_avif_s3_key: 'a.avif',
    };
    process.env.NODE_ENV = 'development';
    const avif = getBestImageUrl(image, {
      supportsAVIF: true,
      supportsWebP: true,
    });
    expect(avif).toBe('https://dev.tylernorlund.com/a.avif');

    const webp = getBestImageUrl(image, {
      supportsAVIF: false,
      supportsWebP: true,
    });
    expect(webp).toBe('https://dev.tylernorlund.com/a.webp');

    const fallback = getBestImageUrl(image, {
      supportsAVIF: false,
      supportsWebP: false,
    });
    expect(fallback).toBe('https://dev.tylernorlund.com/a.png');
  });
});
