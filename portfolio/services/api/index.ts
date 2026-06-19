import {
  ImageDetailsApiResponse,
  CachedImageDetailsResponse,
  LabelValidationCountResponse,
  LabelValidationTimelineResponse,
  LabelValidationResponse,
  LabelEvaluatorResponse,
  MerchantCountsResponse,
  ReceiptApiResponse,
  ImageCountApiResponse,
  ImagesApiResponse,
  ReaderSummaryRequest,
  ReaderSummaryResponse,
  RandomReceiptDetailsResponse,
  AddressSimilarityResponse,
  MilkSimilarityResponse,
  TrainingMetricsResponse,
  LayoutLMBatchInferenceResponse,
  EpochEvaluationResponse,
  EpochEvaluationJobsResponse,
  EpochEvaluationReceiptRecord,
  FinancialMathResponse,
  ReceiptHealthResponse,
  ReceiptHealthIssuesResponse,
  WithinReceiptVerificationResponse,
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
  async submitReaderSummary(
    payload: ReaderSummaryRequest
  ): Promise<ReaderSummaryResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(`${apiUrl}/reader_summary`, {
      method: "POST",
      headers: API_CONFIG.headers,
      body: JSON.stringify(payload),
      keepalive: true,
    });

    if (!response.ok) {
      throw new Error(
        `Failed to submit reader summary: ${response.statusText}`
      );
    }

    return response.json();
  },

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

  // List training jobs that have a per-epoch evaluation available.
  async fetchEpochEvaluationJobs(): Promise<EpochEvaluationJobsResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(`${apiUrl}/layoutlm_epochs`, fetchConfig);
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  // The held-out F1 curve (and training-reported comparison) for one run.
  async fetchEpochEvaluation(job: string): Promise<EpochEvaluationResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/layoutlm_epochs?job=${encodeURIComponent(job)}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  // A single per-(epoch, receipt) showcase record for the scrubber.
  async fetchEpochEvaluationReceipt(
    job: string,
    receiptPath: string
  ): Promise<EpochEvaluationReceiptRecord> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams({ job, receipt_path: receiptPath });
    const response = await fetch(
      `${apiUrl}/layoutlm_epochs?${params.toString()}`,
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
  ): Promise<FinancialMathResponse> {
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

  async fetchWithinReceiptVerification(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0
  ): Promise<WithinReceiptVerificationResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/within_receipt?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchReceiptHealth(
    batchSize: number = 20,
    seed?: number,
    offset: number = 0,
    options: { imageId?: string } = {}
  ): Promise<ReceiptHealthResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    params.set("batch_size", batchSize.toString());
    params.set("offset", offset.toString());
    if (seed !== undefined) {
      params.set("seed", seed.toString());
    }
    if (options.imageId) {
      params.set("image_id", options.imageId);
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/receipt_health?${params.toString()}`,
      fetchConfig
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }
    return response.json();
  },

  async fetchReceiptHealthIssues(options: {
    state?: string;
    checkId?: string;
    imageId?: string;
    receiptId?: number;
    classification?: string;
    lane?: string;
    rootCause?: string;
    executionId?: string;
    limit?: number;
  } = {}): Promise<ReceiptHealthIssuesResponse> {
    const apiUrl = getAPIUrl();
    const params = new URLSearchParams();
    if (options.state) {
      params.set("state", options.state);
    }
    if (options.checkId) {
      params.set("check_id", options.checkId);
    }
    if (options.imageId) {
      params.set("image_id", options.imageId);
    }
    if (options.receiptId !== undefined) {
      params.set("receipt_id", options.receiptId.toString());
    }
    if (options.classification) {
      params.set("classification", options.classification);
    }
    if (options.lane) {
      params.set("lane", options.lane);
    }
    if (options.rootCause) {
      params.set("root_cause", options.rootCause);
    }
    if (options.executionId) {
      params.set("execution_id", options.executionId);
    }
    if (options.limit !== undefined) {
      params.set("limit", options.limit.toString());
    }

    const response = await fetch(
      `${apiUrl}/label_evaluator/receipt_health_issues?${params.toString()}`,
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
};

// Export the API with performance tracking in development
export const api = process.env.NODE_ENV === 'development'
  ? withPerformanceTrackingForAPI(baseApi, 'api')
  : baseApi;
