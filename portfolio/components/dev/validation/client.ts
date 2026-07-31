// Dev-only client for the local validation shim, proxied by the
// /api/validation/:path* rewrite that next.config.js adds in dev.
import {
  MerchantsResponse,
  ReviewEntry,
  ReviewLogResponse,
  ReviewVerdict,
  ValidationReceipt,
  WorklistResponse,
} from "./types";

const BASE = "/api/validation";

const getJson = async <T>(url: string): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(url);
  } catch {
    throw new Error(
      "Local validation shim is unavailable. Start " +
        "portfolio/dev-harness/validation_shim.py on port 8787, then retry.",
    );
  }

  let payload: Record<string, unknown>;
  try {
    payload = (await response.json()) as Record<string, unknown>;
  } catch {
    throw new Error(
      response.ok
        ? `Invalid response from the local validation shim (${url}).`
        : `Local validation shim unavailable (${response.status}).`,
    );
  }
  if (!response.ok || payload?.error) {
    throw new Error(String(payload?.error ?? `${response.status} ${url}`));
  }
  return payload as T;
};

export const fetchMerchants = (refresh = false): Promise<MerchantsResponse> =>
  getJson(`${BASE}/merchants${refresh ? "?refresh=1" : ""}`);

export const fetchWorklist = (
  merchant: string | null,
  status: string,
  limit = 1000,
): Promise<WorklistResponse> =>
  getJson(
    `${BASE}/worklist?status=${encodeURIComponent(status)}&limit=${limit}` +
      (merchant ? `&merchant=${encodeURIComponent(merchant)}` : ""),
  );

export const fetchReviews = (): Promise<ReviewLogResponse> =>
  getJson(`${BASE}/review`);

export const fetchReceipt = (
  imageId: string,
  receiptId: number,
): Promise<ValidationReceipt> =>
  getJson(
    `${BASE}/receipt?image_id=${encodeURIComponent(imageId)}` +
      `&receipt_id=${receiptId}`,
  );

export const postReview = async (entry: {
  image_id: string;
  receipt_id: number;
  verdict: ReviewVerdict;
  note: string;
  merchant: string;
  status: string;
  delta: number | null;
}): Promise<ReviewEntry> => {
  let response: Response;
  try {
    response = await fetch(`${BASE}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entry),
    });
  } catch {
    throw new Error("Could not reach the local validation shim to save review.");
  }
  let payload: Record<string, unknown>;
  try {
    payload = (await response.json()) as Record<string, unknown>;
  } catch {
    throw new Error(`Review save failed (${response.status}).`);
  }
  if (!response.ok || payload?.error) {
    throw new Error(String(payload?.error ?? `${response.status} /review`));
  }
  return payload.entry as ReviewEntry;
};
