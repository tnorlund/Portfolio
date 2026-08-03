// Dev-only client for the local validation shim, proxied by the
// /api/validation/:path* rewrite that next.config.js adds in dev.
import {
  ApproveResponse,
  AuditDeckResponse,
  AuditReceipt,
  AuditReviewResponse,
  DigestResponse,
  QueuesResponse,
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

const postJson = async <T>(path: string, body: unknown): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(
      `Could not reach the local validation shim to POST ${path}.`,
    );
  }
  let payload: Record<string, unknown>;
  try {
    payload = (await response.json()) as Record<string, unknown>;
  } catch {
    throw new Error(`${path} failed (${response.status}).`);
  }
  if (!response.ok || payload?.error) {
    throw new Error(String(payload?.error ?? `${response.status} ${path}`));
  }
  return payload as T;
};

export const fetchQueues = (): Promise<QueuesResponse> =>
  getJson(`${BASE}/queues`);

// A queue is an ordered file of ids, written by the adjudicator when it
// escalates. The escalation screen shows nothing else: there is no browse.
export const fetchWorklist = (queue: string): Promise<WorklistResponse> =>
  getJson(`${BASE}/worklist?queue=${encodeURIComponent(queue)}`);

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

export interface ReviewPayload {
  image_id: string;
  receipt_id: number;
  verdict: ReviewVerdict;
  note: string;
  reason?: string | null;
  line_ids?: number[];
  merchant: string;
  status: string;
  delta: number | null;
  pass_id?: string | null;
}

export const postReview = async (
  entry: ReviewPayload,
): Promise<ReviewEntry> =>
  (await postJson<{ entry: ReviewEntry }>("/review", entry)).entry;

export const fetchDigest = (passId?: string | null): Promise<DigestResponse> =>
  getJson(
    `${BASE}/digest${passId ? `?pass_id=${encodeURIComponent(passId)}` : ""}`,
  );

export const postApprove = (
  passId: string | null,
  groupId: string,
): Promise<ApproveResponse> =>
  postJson("/approve", { pass_id: passId, group_id: groupId });

export const fetchAuditDeck = (
  passId?: string | null,
): Promise<AuditDeckResponse> =>
  getJson(
    `${BASE}/audit${passId ? `?pass_id=${encodeURIComponent(passId)}` : ""}`,
  );

// The shim strips the agent's conclusions from this payload; the deck never
// asks for them, so a UI bug cannot leak the verdict into a blind review.
export const fetchAuditReceipt = (
  imageId: string,
  receiptId: number,
): Promise<AuditReceipt> =>
  getJson(
    `${BASE}/audit?image_id=${encodeURIComponent(imageId)}` +
      `&receipt_id=${receiptId}`,
  );

/** Commit a blind verdict; the response carries the reveal. */
export const postAuditVerdict = (
  entry: ReviewPayload,
): Promise<AuditReviewResponse> => postJson("/review", entry);
