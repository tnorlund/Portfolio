import { useQuery } from "@tanstack/react-query";

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

const fetchImageDetails = async (
  imageType?: string
): Promise<ImageDetailsApiResponse> => {
  // Try to fetch from cache first, then do client-side random selection
  try {
    const cached = await api.fetchCachedImageDetails(imageType);
    if (cached.images && cached.images.length > 0) {
      const randomIndex = Math.floor(Math.random() * cached.images.length);
      return cached.images[randomIndex];
    }
    // Cache is empty, fall back to real-time API
    return await api.fetchRandomImageDetails(imageType);
  } catch {
    // Cache fetch failed (e.g., 404), fall back to real-time API
    return await api.fetchRandomImageDetails(imageType);
  }
};

export const useImageDetails = (
  imageType?: string
): UseImageDetailsResult => {
  const { data, error, isPending } = useQuery({
    queryKey: ["imageDetails", imageType ?? "all"],
    queryFn: async () => {
      const [details, support] = await Promise.all([
        fetchImageDetails(imageType),
        detectImageFormatSupport(),
      ]);
      return { details, support };
    },
  });

  return {
    imageDetails: data?.details ?? null,
    formatSupport: data?.support ?? null,
    error: error ?? null,
    loading: isPending,
  };
};

export default useImageDetails;
