import {
  ImageApiResponse,
  ImageReceiptsLines,
  RootPayload,
  PayloadItem,
  ReceiptApiResponse,
  ReceiptWordsApiResponse,
  ReceiptDetailsApiResponse,
} from "./interfaces";

const isDevelopment = process.env.NODE_ENV === "development";

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
  limit = 5,
  lastEvaluatedKey?: string
): Promise<ReceiptApiResponse> {
  const baseUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipts?limit=${limit}`
    : `https://api.tylernorlund.com/receipts?limit=${limit}`;

  // If lastEvaluatedKey is provided, append it to the query string
  const apiUrl = lastEvaluatedKey
    ? `${baseUrl}&lastEvaluatedKey=${encodeURIComponent(lastEvaluatedKey)}`
    : baseUrl;

  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  const data: ReceiptApiResponse = await response.json();
  return data;
}

export async function fetchReceiptWords(
  tag: string,
  limit: number = 200,
  lastEvaluatedKey?: string
): Promise<ReceiptWordsApiResponse> {
  const baseUrl =
    process.env.NODE_ENV === "development"
      ? `https://dev-api.tylernorlund.com/receipt_word_tag`
      : `https://api.tylernorlund.com/receipt_word_tag`;

  // Build the query string with required parameters.
  let url = `${baseUrl}?tag=${encodeURIComponent(tag)}&limit=${limit}`;
  if (lastEvaluatedKey) {
    url += `&lastEvaluatedKey=${encodeURIComponent(lastEvaluatedKey)}`;
  }

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
