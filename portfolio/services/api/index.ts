import {
  ImageDetailsApiResponse,
  LabelValidationCountResponse,
  MerchantCountsResponse,
  ReceiptApiResponse,
  ImageCountApiResponse,
  ImagesApiResponse,
  RandomReceiptDetailsResponse,
  AddressSimilarityResponse,
} from "../../types/api";
import { withPerformanceTrackingForAPI } from "../../utils/performance/api-wrapper";

// Helper function to get the API URL based on environment
const getAPIUrl = () => {
  const isDevelopment = process.env.NODE_ENV === "development";
  return isDevelopment
    ? "https://dev-api.tylernorlund.com"
    : "https://api.tylernorlund.com";
};

const fetchConfig = {
  headers: {
    "Content-Type": "application/json",
  },
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

  async fetchLayoutLMInference(): Promise<any> {
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
};

// Export the API with performance tracking in development
export const api = process.env.NODE_ENV === 'development'
  ? withPerformanceTrackingForAPI(baseApi, 'api')
  : baseApi;
