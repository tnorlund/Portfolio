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
        const [details, support] = await Promise.all([
          api.fetchRandomImageDetails(imageType),
          detectImageFormatSupport(),
        ]);
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
