import {
  ImageApiResponse,
  ImageReceiptsLines,
  RootPayload,
  PayloadItem,
  ReceiptApiResponse,
  ReceiptWordsApiResponse,
  ReceiptDetailsApiResponse,
  ImageDetailsApiResponse,
  ReceiptWordTagsApiResponse,
  TagValidationStatsResponse,
  ReceiptDetailApiResponse,
  ReceiptWord,
  ReceiptWordTag,
  Word,
  WordTag,
  ReceiptWordTagAction,
} from "./interfaces";

const isDevelopment = process.env.NODE_ENV === "development";

export async function fetchTagValidationStats(): Promise<TagValidationStatsResponse> {
  const apiUrl =
    process.env.NODE_ENV === "development"
      ? `https://dev-api.tylernorlund.com/tag_validation_counts`
      : `https://api.tylernorlund.com/tag_validation_counts`;

      const response = await fetch(apiUrl);

      if (!response.ok) {
        throw new Error(`Network response was not ok (status: ${response.status})`);
      }
    
      return await response.json();
}

export const postReceiptWordTag = async (params: ReceiptWordTagAction) => {
  const apiUrl =
    process.env.NODE_ENV === "development"
      ? `https://dev-api.tylernorlund.com/receipt_word_tag`
      : `https://api.tylernorlund.com/receipt_word_tag`;

  // Validate required parameters based on action
  if ((params.action === "change_tag" || params.action === "add_tag") && !params.new_tag) {
    throw new Error(`new_tag is required for ${params.action} action`);
  }

  const response = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  });
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(
      errorData?.error || 
      `Failed to update tag (status: ${response.status})`
    );
  }
  
  return await response.json();
};

export const postReceiptWordTags = async (params: {
  selected_tag: string;
  selected_words: Array<{
    word: ReceiptWord;
    tags: ReceiptWordTag[];
  }>;
}): Promise<{
  updated_items: Array<{
    word: Word;
    word_tag: WordTag;
    receipt_word: ReceiptWord;
    receipt_word_tag: ReceiptWordTag;
  }>;
}> => {
  const apiUrl = process.env.NODE_ENV === "development"
    ? `https://dev-api.tylernorlund.com/receipt_word_tags`
    : `https://api.tylernorlund.com/receipt_word_tags`;

  const response = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(
      errorData?.error || 
      `Failed to update tags (status: ${response.status})`
    );
  }

  return await response.json();
};

export async function fetchReceiptWordTags(
  tag: string,
  limit: number,
  lastEvaluatedKey?: any
): Promise<ReceiptWordTagsApiResponse> {
  const params = new URLSearchParams();
  params.set("limit", limit.toString());
  params.set("tag", tag);
  if (lastEvaluatedKey) {
    // JSON-encode the object exactly once.
    params.set("lastEvaluatedKey", JSON.stringify(lastEvaluatedKey));
  }
  const baseUrl =
    process.env.NODE_ENV === "development"
      ? `https://dev-api.tylernorlund.com/receipt_word_tags`
      : `https://api.tylernorlund.com/receipt_word_tags`;

  const url = `${baseUrl}?${params.toString()}`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  return await response.json();
}

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

export async function fetchReceiptDetail(
  imageId: string,
  receiptId: number
): Promise<ReceiptDetailApiResponse> {
  const params = new URLSearchParams();
  params.set("image_id", imageId);
  params.set("receipt_id", receiptId.toString());
  const baseUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipt_detail`
    : `https://api.tylernorlund.com/receipt_detail`;

  const url = `${baseUrl}?${params.toString()}`;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Network response was not ok (status: ${response.status})`);
    }

    const data: ReceiptDetailApiResponse = await response.json();
    return data;
  } catch (error) {
    console.error("Error fetching receipt detail:", error);
    throw error;
  }
}

export async function fetchReceiptDetails(
  limit = 5,
  lastEvaluatedKey?: any
): Promise<ReceiptDetailsApiResponse> {
  const params = new URLSearchParams();
  params.set("limit", limit.toString());
  
  // Only add lastEvaluatedKey to params if it exists
  if (lastEvaluatedKey) {
    params.set("last_evaluated_key", JSON.stringify(lastEvaluatedKey));
  }
  
  const baseUrl = isDevelopment
    ? `https://dev-api.tylernorlund.com/receipt_details`
    : `https://api.tylernorlund.com/receipt_details`;

  const url = `${baseUrl}?${params.toString()}`;

  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Network response was not ok (status: ${response.status})`);
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
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Error: ${response.status}`);
  }
  return await response.json();
}

export async function fetchReceiptWordPaginate(
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
      ? `https://dev-api.tylernorlund.com/receipt_word_tag_page`
      : `https://api.tylernorlund.com/receipt_word_tag_page`;

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
