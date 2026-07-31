// Dev-only client for the local validation shim, proxied by the
// /api/validation/:path* rewrite that next.config.js adds in dev.
import {
  MerchantsResponse,
  ReviewEntry,
  ReviewVerdict,
  ValidationReceipt,
  WorklistResponse,
} from "./types";

const BASE = "/api/validation";

const getJson = async <T>(url: string): Promise<T> => {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok || payload?.error) {
    throw new Error(payload?.error ?? `${response.status} ${url}`);
  }
  return payload as T;
};

export const fetchMerchants = (refresh = false): Promise<MerchantsResponse> =>
  getJson(`${BASE}/merchants${refresh ? "?refresh=1" : ""}`);

export const fetchWorklist = (
  merchant: string | null,
  status: string,
): Promise<WorklistResponse> =>
  getJson(
    `${BASE}/worklist?status=${encodeURIComponent(status)}` +
      (merchant ? `&merchant=${encodeURIComponent(merchant)}` : ""),
  );

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
  const response = await fetch(`${BASE}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  const payload = await response.json();
  if (!response.ok || payload?.error) {
    throw new Error(payload?.error ?? `${response.status} /review`);
  }
  return payload.entry as ReviewEntry;
};
