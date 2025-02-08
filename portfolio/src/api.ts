import {
  ImageApiResponse,
  ImageReceiptsLines,
  RootPayload,
  PayloadItem,
  ReceiptApiResponse,
  ReceiptWordsApiResponse,
  ReceiptDetailsApiResponse,
  ImageDetailsApiResponse,
} from "./interfaces";

const isDevelopment = process.env.NODE_ENV === "development";

export async function fetchWordTagList(): Promise<string[]> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/word_tag_list`
    : `https://api.tylernorlund.com/word_tag_list`;
  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

export async function fetchImageCount(): Promise<number> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/image_count`
    : `https://api.tylernorlund.com/image_count`;
  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

export async function fetchReceiptCount(): Promise<number> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipt_count`
    : `https://api.tylernorlund.com/receipt_count`;
  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

export async function fetchImageDetails(): Promise<ImageDetailsApiResponse> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/image_details`
    : `https://api.tylernorlund.com/image_details`;
  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

/** Fetch images from the API and return a list of tuples */
export async function fetchImages(limit = 5): Promise<ImageReceiptsLines[]> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/images?limit=${limit}`
    : `https://api.tylernorlund.com/images?limit=${limit}`;
  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  const data: ImageApiResponse = await response.json();
  return mapPayloadToImages(data.payload);
}

export async function fetchReceiptDetails(
  limit = 5
): Promise<ReceiptDetailsApiResponse> {
  const apiUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipt_details?limit=${limit}`
    : `https://api.tylernorlund.com/receipt_details?limit=${limit}`;

  try {
    const response = await fetch(apiUrl);
    if (!response.ok) {
      throw new Error(
        `Network response was not ok (status: ${response.status})`
      );
    }

    const data: ReceiptDetailsApiResponse = await response.json();
    return data;
  } catch (error) {
    console.error("Error fetching receipt details:", error);
    throw error;
  }
}

export async function fetchReceipts(
  limit: number,
  lastEvaluatedKey?: any  // Changed from string to any
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
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Error: ${response.status}`);
  }
  return await response.json();
}

export async function fetchReceiptWords(
  tag: string,
  limit: number,
  lastEvaluatedKey?: any
): Promise<ReceiptWordsApiResponse> {
  const params = new URLSearchParams();
  params.set("tag", tag);
  params.set("limit", limit.toString());
  if (lastEvaluatedKey) {
    // Encode the lastEvaluatedKey as a JSON string.
    params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
  }

  const baseUrl =
    process.env.NODE_ENV === "development"
      ? `https://dev-api.tylernorlund.com/receipt_word_tag`
      : `https://api.tylernorlund.com/receipt_word_tag`;

  const url = `${baseUrl}?${params.toString()}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Error fetching receipt words: ${response.statusText}`);
  }
  const data = (await response.json()) as ReceiptWordsApiResponse;
  return data;
}

/** Map RootPayload to [ImagePayload, Receipt[], LineItem[]] tuples */
export function mapPayloadToImages(payload: RootPayload): ImageReceiptsLines[] {
  return Object.values(payload).map((item: PayloadItem) => {
    const { image, receipts, lines } = item;

    return [image, receipts || [], lines || []];
  });
}
