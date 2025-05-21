import {
  ImageDetailsApiResponse,
  LabelValidationCountResponse,
  MerchantCountsResponse,
  ReceiptApiResponse,
} from "./interfaces";

const isDevelopment = process.env.NODE_ENV === "development";

// Remove the credentials config
const fetchConfig = {
  credentials: "include" as RequestCredentials,
  headers: {
    "Content-Type": "application/json",
  },
};

export async function fetchMerchantCounts(): Promise<MerchantCountsResponse> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/merchant_counts`
    : `https://api.tylernorlund.com/merchant_counts`;
  const response = await fetch(apiUrl, fetchConfig);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

export async function fetchLabelValidationCount(): Promise<LabelValidationCountResponse> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/label_validation_count`
    : `https://api.tylernorlund.com/label_validation_count`;
  const response = await fetch(apiUrl, fetchConfig);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}


export async function fetchImageCount(): Promise<number> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/image_count`
    : `https://api.tylernorlund.com/image_count`;
  const response = await fetch(apiUrl, fetchConfig);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

export async function fetchReceiptCount(): Promise<number> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipt_count`
    : `https://api.tylernorlund.com/receipt_count`;
  const response = await fetch(apiUrl, fetchConfig);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

export async function fetchImageDetails(): Promise<ImageDetailsApiResponse> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/image_details`
    : `https://api.tylernorlund.com/image_details`;
  const response = await fetch(apiUrl, fetchConfig);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

/** Fetch images from the API and return a list of tuples */

export async function fetchReceipts(
  limit: number,
  lastEvaluatedKey?: any // Changed from string to any
): Promise<ReceiptApiResponse> {
  const params = new URLSearchParams();
  params.set("limit", limit.toString());
  if (lastEvaluatedKey) {
    // JSON-encode the object exactly once.
    params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
  }
  const baseUrl =
    process.env.NODE_ENV === "development"
      ? `https://dev-api.tylernorlund.com/receipts`
      : `https://api.tylernorlund.com/receipts`;

  const url = `${baseUrl}?${params.toString()}`;
  const response = await fetch(url, fetchConfig);
  if (!response.ok) {
    throw new Error(`Error: ${response.status}`);
  }
  return await response.json();
}


