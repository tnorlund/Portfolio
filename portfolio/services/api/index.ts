import {
  ImageDetailsApiResponse,
  CachedImageDetailsResponse,
  LabelValidationCountResponse,
  LabelValidationTimelineResponse,
  LabelValidationResponse,
  MerchantCountsResponse,
  ReceiptApiResponse,
  ImageCountApiResponse,
  ImagesApiResponse,
  RandomReceiptDetailsResponse,
  AddressSimilarityResponse,
  WordSimilarityResponse,
  MilkSimilarityResponse,
  TrainingMetricsResponse,
  LayoutLMBatchInferenceResponse,
  LabelEvaluatorResponse,
  LabelEvaluatorFinancialMathResponse,
  LabelEvaluatorDiffResponse,
  LabelEvaluatorJourneyResponse,
  LabelEvaluatorPatternsResponse,
  LabelEvaluatorEvidenceResponse,
  LabelEvaluatorDedupResponse,
} from "../../types/api";
import { withPerformanceTrackingForAPI } from "../../utils/performance/api-wrapper";
import { API_CONFIG } from "./config";

// Use centralized API config for URL - handles dev proxy, test, and production
const getAPIUrl = () => API_CONFIG.baseUrl;

const fetchConfig = {
  headers: API_CONFIG.headers,
};

// API calls that go directly to external APIs
const baseApi = {
  async fetchImageCount(): Promise<ImageCountApiResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(`${apiUrl}/image_count`, fetchConfig);
    if (!response.ok) {
      throw new Error(`Failed to fetch image count: ${response.statusText}`);
    }
    const count = await response.json();
    // Wrap the count in the expected response format
    return { count };
  },

  async fetchReceiptCount(): Promise<number> {
    const apiUrl = getAPIUrl();
    const response = await fetch(`${apiUrl}/receipt_count`, fetchConfig);
    if (!response.ok) {
      throw new Error(`Failed to fetch receipt count: ${response.statusText}`);
    }
    return response.json();
  },

  async fetchMerchantCounts(): Promise<MerchantCountsResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(`${apiUrl}/merchant_counts`, fetchConfig);
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelValidationCount(): Promise<LabelValidationCountResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/label_validation_count`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelValidationTimeline(): Promise<LabelValidationTimelineResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/label_validation_timeline`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchRandomImageDetails(
    imageType?: string
  ): Promise<ImageDetailsApiResponse> {
    const params = new URLSearchParams();
    if (imageType) {
      params.set("image_type", imageType);
    }

    const apiUrl = getAPIUrl();
    const queryString = params.toString();
    const url = queryString
      ? `${apiUrl}/random_image_details?${queryString}`
      : `${apiUrl}/random_image_details`;
    const response = await fetch(url, fetchConfig);
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchCachedImageDetails(
    imageType?: string
  ): Promise<CachedImageDetailsResponse> {
    const params = new URLSearchParams();
    if (imageType) {
      params.set("image_type", imageType);
    }

    const apiUrl = getAPIUrl();
    const queryString = params.toString();
    const url = queryString
      ? `${apiUrl}/image_details_cache?${queryString}`
      : `${apiUrl}/image_details_cache`;
    const response = await fetch(url, fetchConfig);
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchReceipts(
    limit: number,
    lastEvaluatedKey?: any
  ): Promise<ReceiptApiResponse> {
    const params = new URLSearchParams();
    params.set("limit", limit.toString());
    if (lastEvaluatedKey) {
      params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
    }

    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/receipts?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`);
    }
    return response.json();
  },

  async fetchImagesByType(
    imageType: string,
    limit?: number,
    lastEvaluatedKey?: any
  ): Promise<ImagesApiResponse> {
    const params = new URLSearchParams();
    params.set("image_type", imageType);
    if (limit !== undefined) {
      params.set("limit", limit.toString());
    }
    if (lastEvaluatedKey) {
      params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
    }

    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/images?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`);
    }
    return response.json();
  },

  async fetchImages(
    limit?: number,
    lastEvaluatedKey?: any
  ): Promise<ImagesApiResponse> {
    const params = new URLSearchParams();
    if (limit !== undefined) {
      params.set("limit", limit.toString());
    }
    if (lastEvaluatedKey) {
      params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
    }

    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/images?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`);
    }
    return response.json();
  },

  async fetchRandomReceiptDetails(): Promise<RandomReceiptDetailsResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/random_receipt_details`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchAddressSimilarity(): Promise<AddressSimilarityResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/address_similarity`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchWordSimilarity(): Promise<MilkSimilarityResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/word_similarity`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLayoutLMInference(): Promise<LayoutLMBatchInferenceResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/layoutlm_inference`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchFeaturedTrainingMetrics(): Promise<TrainingMetricsResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/jobs/featured/training-metrics?collapse_bio=true`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorVisualization(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/visualization?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorFinancialMath(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorFinancialMathResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/financial_math?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorDiff(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorDiffResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/diff?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorJourney(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorJourneyResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/journey?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorPatterns(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorPatternsResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/patterns?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorEvidence(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorEvidenceResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/evidence?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelEvaluatorDedup(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelEvaluatorDedupResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/dedup?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchLabelValidationVisualization(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<LabelValidationResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_validation/visualization?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },
};

// Export the API with performance tracking in development
export const api = process.env.NODE_ENV === 'development'
  ? withPerformanceTrackingForAPI(baseApi, 'api')
  : baseApi;
