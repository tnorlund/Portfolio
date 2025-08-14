export interface FormatSupport {
  supportsAVIF: boolean;
  supportsWebP: boolean;
}

export const detectImageFormatSupport = (): Promise<FormatSupport> => {
  return new Promise((resolve) => {
    const userAgent = navigator.userAgent;

    const getSafariVersion = (): number | null => {
      if (userAgent.includes("Chrome")) return null;
      const safariMatch = userAgent.match(/Version\/([0-9.]+).*Safari/);
      if (safariMatch) {
        return parseFloat(safariMatch[1]);
      }
      return null;
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
      } catch {
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
      return new Promise((resolveAVIF) => {
        const img = new Image();
        img.onload = () => resolveAVIF(true);
        img.onerror = () => resolveAVIF(false);
        img.src =
          "data:image/avif;base64,AAAAIGZ0eXBhdmlmAAAAAGF2aWZtaWYxbWlhZk1BMUIAAADybWV0YQAAAAAAAAAoaGRscgAAAAAAAAAAcGljdAAAAAAAAAAAAAAAAGxpYmF2aWYAAAAADnBpdG0AAAAAAAEAAAAeaWxvYwAAAABEAAABAAEAAAABAAABGgAAAB0AAAAoaWluZgAAAAAAAQAAABppbmZlAgAAAABAABhdjAxQ29sb3IAAAAAamlwcnAAAABLaXBjbwAAABRpc3BlAAAAAAAAAAEAAAABAAAAEHBpeGkAAAAAAwgICAAAAAxhdjFDgQ0MAAAAABNjb2xybmNseAACAAIAAYAAAAAXaXBtYQAAAAAAAAABAAEEAQKDBAAAACVtZGF0EgAKCBgABogQEAwgMg8f8D///8WfhwB8+ErK42A=";
      });
    };

    detectAVIF().then((supportsAVIF) => {
      resolve({ supportsAVIF, supportsWebP });
    });
  });
};

import type { Image } from "../types/api";

const isDevelopment = process.env.NODE_ENV === "development";

export const getBestImageUrl = (
  image: Image,
  formatSupport: FormatSupport
): string => {
  const baseUrl = isDevelopment
    ? "https://dev.tylernorlund.com"
    : "https://www.tylernorlund.com";

  if (formatSupport.supportsAVIF && image.cdn_avif_s3_key) {
    return `${baseUrl}/${image.cdn_avif_s3_key}`;
  }
  if (formatSupport.supportsWebP && image.cdn_webp_s3_key) {
    return `${baseUrl}/${image.cdn_webp_s3_key}`;
  }
  return `${baseUrl}/${image.cdn_s3_key}`;
};
