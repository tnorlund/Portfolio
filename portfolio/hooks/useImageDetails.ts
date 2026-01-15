import { useEffect, useState } from "react";

import { api } from "../services/api";
import type { ImageDetailsApiResponse } from "../types/api";
import type { FormatSupport } from "../utils/image";
import { detectImageFormatSupport } from "../utils/image";

interface UseImageDetailsResult {
  imageDetails: ImageDetailsApiResponse | null;
  formatSupport: FormatSupport | null;
  error: Error | null;
  loading: boolean;
}

export const useImageDetails = (
  imageType?: string
): UseImageDetailsResult => {
  const [imageDetails, setImageDetails] = useState<ImageDetailsApiResponse | null>(null);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    const load = async () => {
      try {
        // Try to fetch from cache first, then do client-side random selection
        let details: ImageDetailsApiResponse;
        try {
          const cached = await api.fetchCachedImageDetails(imageType);
          if (cached.images && cached.images.length > 0) {
            // Client-side random selection from cached pool
            const randomIndex = Math.floor(Math.random() * cached.images.length);
            details = cached.images[randomIndex];
          } else {
            // Cache is empty, fall back to real-time API
            details = await api.fetchRandomImageDetails(imageType);
          }
        } catch (cacheError) {
          // Cache fetch failed (e.g., 404), fall back to real-time API
          details = await api.fetchRandomImageDetails(imageType);
        }

        const support = await detectImageFormatSupport();

        if (isMounted) {
          setImageDetails(details);
          setFormatSupport(support);
        }
      } catch (err) {
        if (isMounted) {
          setError(err as Error);
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };
    load();
    return () => {
      isMounted = false;
    };
  }, [imageType]);

  return { imageDetails, formatSupport, error, loading };
};

export default useImageDetails;
