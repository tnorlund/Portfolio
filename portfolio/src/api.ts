import { ImageApiResponse, ImageReceiptsLines, RootPayload, PayloadItem, ReceiptApiResponse } from './interfaces';

const isDevelopment = process.env.NODE_ENV === 'development';

/** Fetch images from the API and return a list of tuples */
export async function fetchImages(limit=5): Promise<ImageReceiptsLines[]> {
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

export async function fetchReceipts(limit=5): Promise<ReceiptApiResponse> {
  // Customize the endpoint to match your receipts API
  const baseUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipts?limit=${limit}`
    : `https://api.tylernorlund.com/receipts?limit=${limit}`;

  const response = await fetch(baseUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  // Parse JSON as our ReceiptApiResponse
  const data: ReceiptApiResponse = await response.json();
  return data;
}

/** Map RootPayload to [ImagePayload, Receipt[], LineItem[]] tuples */
export function mapPayloadToImages(payload: RootPayload): ImageReceiptsLines[] {
  return Object.values(payload).map((item: PayloadItem) => {
    const { image, receipts, lines } = item;

    return [image, receipts || [], lines || []];
  });
}