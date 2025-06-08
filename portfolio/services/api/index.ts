import {
  ImageDetailsApiResponse,
  LabelValidationCountResponse,
  MerchantCountsResponse,
  ReceiptApiResponse,
  ImageCountApiResponse,
  ImagesApiResponse,
} from "../../types/api";

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
export const api = {
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
        `Network response was not ok (status: ${response.status})`,
      );
    }
    return response.json();
  },

  async fetchLabelValidationCount(): Promise<LabelValidationCountResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/label_validation_count`,
      fetchConfig,
    );
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`,
      );
    }
    return response.json();
  },

  async fetchImageDetails(): Promise<ImageDetailsApiResponse> {
    const apiUrl = getAPIUrl();
    const response = await fetch(`${apiUrl}/image_details`, fetchConfig);
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`,
      );
    }
    return response.json();
  },

  async fetchReceipts(
    limit: number,
    lastEvaluatedKey?: any,
  ): Promise<ReceiptApiResponse> {
    const params = new URLSearchParams();
    params.set("limit", limit.toString());
    if (lastEvaluatedKey) {
      params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
    }

    const apiUrl = getAPIUrl();
    const response = await fetch(
      `${apiUrl}/receipts?${params.toString()}`,
      fetchConfig,
    );
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`);
    }
    return response.json();
  },

  async fetchImagesByType(
    imageType: string,
    limit?: number,
    lastEvaluatedKey?: any,
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
      fetchConfig,
    );
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`);
    }
    return response.json();
  },
};
