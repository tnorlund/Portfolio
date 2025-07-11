export interface FormatSupport {
  supportsAVIF: boolean;
  supportsWebP: boolean;
}

const CACHE_KEY = 'imageFormatSupport';
const CACHE_DURATION = 7 * 24 * 60 * 60 * 1000; // 7 days in milliseconds

/**
 * Detect browser support for AVIF and WebP image formats.
 * Results are cached in localStorage for performance.
 */
export const detectImageFormatSupport = (): Promise<FormatSupport> => {
  return new Promise(resolve => {
    // Check localStorage cache first
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (cached) {
        const { data, timestamp } = JSON.parse(cached);
        const age = Date.now() - timestamp;
        if (age < CACHE_DURATION) {
          resolve(data);
          return;
        }
      }
    } catch (e) {
      // Ignore localStorage errors
    }

    const userAgent = navigator.userAgent;

    const getSafariVersion = (): number | null => {
      if (userAgent.includes("Chrome")) return null;
      const safariMatch = userAgent.match(/Version\/([0-9.]+).*Safari/);
      return safariMatch ? parseFloat(safariMatch[1]) : null;
    };

    const isChrome = userAgent.includes("Chrome") && userAgent.includes("Google Chrome");
    const isFirefox = userAgent.includes("Firefox");
    const safariVersion = getSafariVersion();
    const isSafari = safariVersion !== null;

    let supportsWebP = false;

    if (isChrome || isFirefox) {
      supportsWebP = true;
    } else if (isSafari && safariVersion && safariVersion >= 14) {
      supportsWebP = true;
    } else {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          const webpDataUrl = canvas.toDataURL("image/webp", 0.5);
          supportsWebP = webpDataUrl.indexOf("data:image/webp") === 0;
        }
      } catch (e) {
        supportsWebP = false;
      }
    }

    const detectAVIF = (): Promise<boolean> => {
      if (isChrome) {
        const chromeMatch = userAgent.match(/Chrome\/([0-9]+)/);
        if (chromeMatch && parseInt(chromeMatch[1]) >= 85) {
          return Promise.resolve(true);
        }
      }

      if (isFirefox) {
        const firefoxMatch = userAgent.match(/Firefox\/([0-9]+)/);
        if (firefoxMatch && parseInt(firefoxMatch[1]) >= 93) {
          return Promise.resolve(true);
        }
      }

      if (isSafari && safariVersion && safariVersion >= 16.4) {
        return Promise.resolve(true);
      }

      return new Promise(resolveAVIF => {
        const img = new Image();
        img.onload = () => resolveAVIF(true);
        img.onerror = () => resolveAVIF(false);
        img.src =
          "data:image/avif;base64,AAAAIGZ0eXBhdmlmAAAAAGF2aWZtaWYxbWlhZk1BMUIAAA" +
          "DybWV0YQAAAAAAAAAoaGRscgAAAAAAAAAAcGljdAAAAAAAAAAAAAAAAGxpYmF2aWYAAAAADnBpdG0AAA" +
          "AAAAEAAAAeaWxvYwAAAABEAAABAAEAAAABAAABGgAAAB0AAAAoaWluZgAAAAAAAQAAABppbmZlAgAAAA" +
          "ABAABhdjAxQ29sb3IAAAAAamlwcnAAAABLaXBjbwAAABRpc3BlAAAAAAAAAAEAAAABAAAAEHBpeGkAAA" +
          "AAAwgICAAAAAxhdjFDgQ0MAAAAABNjb2xybmNseAACAAIAAYAAAAAXaXBtYQAAAAAAAAABAAEEAQKDBA" +
          "AAACVtZGF0EgAKCBgABogQEAwgMg8f8D///8WfhwB8+ErK42A=";
      });
    };

    detectAVIF().then(supportsAVIF => {
      const result = { supportsAVIF, supportsWebP };
      
      // Cache the result
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({
          data: result,
          timestamp: Date.now()
        }));
      } catch (e) {
        // Ignore localStorage errors
      }
      
      resolve(result);
    });
  });
};

export interface ImageFormats {
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  // Thumbnail versions
  cdn_thumbnail_s3_key?: string;
  cdn_thumbnail_webp_s3_key?: string;
  cdn_thumbnail_avif_s3_key?: string;
  // Small versions
  cdn_small_s3_key?: string;
  cdn_small_webp_s3_key?: string;
  cdn_small_avif_s3_key?: string;
  // Medium versions
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
}

export type ImageSize = 'thumbnail' | 'small' | 'medium' | 'full';

/**
 * Choose the optimal image URL given supported formats and available keys.
 * Now supports different image sizes for bandwidth optimization.
 */
export const getBestImageUrl = (
  image: ImageFormats,
  formatSupport: FormatSupport,
  size: ImageSize = 'full',
): string => {
  const baseUrl =
    process.env.NODE_ENV === "development"
      ? "https://dev.tylernorlund.com"
      : "https://www.tylernorlund.com";

  const browserName = navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome') ? 'Safari' : 'Chrome';

  // Helper to get the appropriate key based on size and format
  const getKey = (format: 'jpeg' | 'webp' | 'avif'): string | undefined => {
    switch (size) {
      case 'thumbnail':
        return format === 'jpeg' ? image.cdn_thumbnail_s3_key :
               format === 'webp' ? image.cdn_thumbnail_webp_s3_key :
               image.cdn_thumbnail_avif_s3_key;
      case 'small':
        return format === 'jpeg' ? image.cdn_small_s3_key :
               format === 'webp' ? image.cdn_small_webp_s3_key :
               image.cdn_small_avif_s3_key;
      case 'medium':
        return format === 'jpeg' ? image.cdn_medium_s3_key :
               format === 'webp' ? image.cdn_medium_webp_s3_key :
               image.cdn_medium_avif_s3_key;
      case 'full':
      default:
        return format === 'jpeg' ? image.cdn_s3_key :
               format === 'webp' ? image.cdn_webp_s3_key :
               image.cdn_avif_s3_key;
    }
  };

  console.log(`[${browserName}] Format support:`, formatSupport);
  console.log(`[${browserName}] Available keys:`, {
    avif: getKey('avif'),
    webp: getKey('webp'),
    jpeg: getKey('jpeg'),
  });

  // Try AVIF first if supported
  if (formatSupport.supportsAVIF) {
    const avifKey = getKey('avif');
    if (avifKey) {
      console.log(`[${browserName}] Selected format: AVIF (${size})`);
      console.log(`[${browserName}] URL: ${baseUrl}/${avifKey}`);
      return `${baseUrl}/${avifKey}`;
    }
    console.log(`[${browserName}] AVIF supported but no key available`);
  }

  // Try WebP if supported
  if (formatSupport.supportsWebP) {
    const webpKey = getKey('webp');
    if (webpKey) {
      console.log(`[${browserName}] Selected format: WebP (${size})`);
      console.log(`[${browserName}] URL: ${baseUrl}/${webpKey}`);
      return `${baseUrl}/${webpKey}`;
    }
    console.log(`[${browserName}] WebP supported but no key available`);
  }

  // Fallback to JPEG
  const jpegKey = getKey('jpeg');
  if (jpegKey) {
    console.log(`[${browserName}] Selected format: JPEG (${size})`);
    console.log(`[${browserName}] URL: ${baseUrl}/${jpegKey}`);
    return `${baseUrl}/${jpegKey}`;
  }

  // Ultimate fallback to full-size JPEG if specific size not available
  console.log(`[${browserName}] Selected format: JPEG (full fallback)`);
  console.log(`[${browserName}] URL: ${baseUrl}/${image.cdn_s3_key}`);
  return `${baseUrl}/${image.cdn_s3_key}`;
};
