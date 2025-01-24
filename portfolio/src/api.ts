import { ApiResponse, ImageReceiptsLines, RootPayload, PayloadItem } from './interfaces';

const isDevelopment = process.env.NODE_ENV === 'development';
const apiUrl = isDevelopment
  ? 'https://dev-api.tylernorlund.com/images?limit=25'
  : 'https://api.tylernorlund.com/images?limit=25';

/** Fetch images from the API and return a list of tuples */
export async function fetchImages(): Promise<ImageReceiptsLines[]> {
  const response = await fetch(apiUrl);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  const data: ApiResponse = await response.json();
  return mapPayloadToImages(data.payload);
}

/** Map RootPayload to [ImagePayload, Receipt[], LineItem[]] tuples */
export function mapPayloadToImages(payload: RootPayload): ImageReceiptsLines[] {
  return Object.values(payload).map((item: PayloadItem) => {
    const { image, receipts, lines } = item;

    return [image, receipts || [], lines || []];
  });
}