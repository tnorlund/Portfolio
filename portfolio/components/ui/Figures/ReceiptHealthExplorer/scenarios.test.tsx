import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../../../../services/api";
import type {
  ReceiptHealthCheck,
  ReceiptHealthLedgerIssue,
  ReceiptHealthReceipt,
  ReceiptHealthResponse,
} from "../../../../types/api";
import ReceiptHealthExplorer from "./index";
import {
  CHECK_ORDER,
  loadReceiptHealthExamples,
  preferredCheckIdForReceipt,
  selectReceiptHealthExamples,
} from "./scenarios";

jest.mock("../../../../services/api", () => ({ api: {
  fetchReceiptHealth: jest.fn(),
  fetchReceiptHealthIssues: jest.fn(),
} }));
jest.mock("react-intersection-observer", () => {
  const ref = () => {};
  return { useInView: ({ triggerOnce }: { triggerOnce: boolean }) => ({ ref, inView: triggerOnce }) };
});
jest.mock("../../../../utils/imageFormat", () => ({
  getBestImageUrl: () => "/receipt.png",
  getJpegFallbackUrl: () => "/receipt.png",
  usePreloadReceiptImages: () => {},
}));
jest.mock("../ReceiptFlow/useImageFormatSupport", () => {
  const support = { avif: false, webp: true };
  return { useImageFormatSupport: () => support };
});
jest.mock("../ReceiptFlow/useFlyingReceipt", () => ({
  useFlyingReceipt: () => ({ flyingItem: null, showFlying: false }),
}));

const fetchHealth = jest.mocked(api.fetchReceiptHealth);
const fetchIssues = jest.mocked(api.fetchReceiptHealthIssues);

function receipt(
  imageId: string,
  failingChecks: ReceiptHealthCheck["id"][] = [],
  receiptId = 1,
): ReceiptHealthReceipt {
  return {
    image_id: imageId, receipt_id: receiptId, merchant_name: imageId,
    trace_id: null, width: 300, height: 500, cdn_s3_key: "receipt.png", words: [],
    overall_status: failingChecks.length ? "fail" : "pass",
    summary: {
      total_checks: 3, passed: 3 - failingChecks.length,
      failed: failingChecks.length, needs_review: 0, not_applicable: 0,
      issue_count: failingChecks.length,
    },
    checks: CHECK_ORDER.map((id) => ({
      id, title: id, question: id, status: failingChecks.includes(id) ? "fail" : "pass",
      validator: id === "merchant_identity" ? "place_validation"
        : id === "receipt_format" ? "format_validation" : "financial_math",
      is_llm: false, duration_seconds: null, result: id, evidence_count: 0,
      summary: { total: 1, valid: 1, invalid: 0, needs_review: 0 }, what_it_validates: [],
    })),
    primary_issues: [],
    place_validation: {
      place: null, decisions: [], duration_seconds: null, is_llm: false,
      summary: { total: 0, valid: 0, invalid: 0, needs_review: 0 },
    },
    format_validation: {
      decisions: [], duration_seconds: null, is_llm: false,
      summary: { total: 0, valid: 0, invalid: 0, needs_review: 0 },
    },
    financial_math: {
      equations: [], duration_seconds: null, is_llm: false,
      summary: { total_equations: 0, has_invalid: false, has_needs_review: false },
    },
  };
}

function issue(
  candidate: ReceiptHealthReceipt,
  checkId: ReceiptHealthCheck["id"],
  classification = "needs_ai_review",
  rootCause = "unclassified",
): ReceiptHealthLedgerIssue {
  return {
    issue_id: `${candidate.image_id}-${checkId}`, fingerprint: "test", execution_id: "test",
    observed_at: "2026-09-29T00:00:00+00:00", image_id: candidate.image_id,
    receipt_id: candidate.receipt_id, check_id: checkId, check_title: checkId,
    validator: "financial_math", status: "fail", issue_type: "test", message: "test",
    evidence: [], state: "open", preflight: {
      version: "1", classification, root_cause: rootCause, automation_lane: "none",
      lane: "none", is_automation_ready: false, summary: "test", proposed_actions: [], action_count: 0,
    },
  };
}

function response(receipts: ReceiptHealthReceipt[]): ReceiptHealthResponse {
  return {
    receipts, total_count: receipts.length, offset: 0, has_more: false, seed: 29,
    aggregate_stats: {
      total_receipts_in_pool: receipts.length, batch_size: receipts.length,
      passed: 0, needs_review: 0, failed: 0, not_applicable: 0,
      receipts_with_issues: 0, total_issues: 0,
    },
  };
}

function mockLedger(issues: ReceiptHealthLedgerIssue[]) {
  fetchIssues.mockImplementation(async (options = {}) => ({ issues: issues.filter((entry) =>
    (!options.imageId || entry.image_id === options.imageId) &&
    (options.receiptId === undefined || entry.receipt_id === options.receiptId) &&
    (!options.checkId || entry.check_id === options.checkId) &&
    (!options.classification || entry.preflight?.classification === options.classification) &&
    (!options.rootCause || entry.preflight?.root_cause === options.rootCause),
  ) }));
}

beforeEach(() => {
  jest.resetAllMocks();
  fetchIssues.mockResolvedValue({ issues: [] });
});

test("selects six distinct, evidence-backed scenarios with the intended check focus", () => {
  const clean = receipt("clean");
  const merchant = receipt("merchant", ["merchant_identity"]);
  const format = receipt("format", ["receipt_format"]);
  const math = receipt("math", ["financial_math"]);
  const ocr = receipt("ocr", ["financial_math"]);
  const consistent = receipt("consistent", ["financial_math"]);
  const examples = selectReceiptHealthExamples(
    [ocr, consistent, math, clean, merchant, format, clean],
    [issue(ocr, "financial_math", "reocr_needed"),
      issue(consistent, "financial_math", "known_limitation", "already_consistent_labels")],
  );
  expect(examples.map((example) => [example.scenario?.label, example.image_id, preferredCheckIdForReceipt(example)]))
    .toEqual([
      ["Clean", "clean", "merchant_identity"], ["Merchant", "merchant", "merchant_identity"],
      ["Format", "format", "receipt_format"], ["Math", "math", "financial_math"],
      ["OCR gap", "ocr", "financial_math"], ["Consistent", "consistent", "financial_math"],
    ]);
});

test("keeps general fallbacks without assigning unsupported or resolved scenario labels", () => {
  const healthy = receipt("healthy");
  const secondHealthy = receipt("other");
  const failed = receipt("failed", ["financial_math"]);
  const resolved = { ...issue(failed, "financial_math", "reocr_needed"), state: "resolved" as const };
  const examples = selectReceiptHealthExamples([healthy, secondHealthy, failed], [resolved]);
  expect(examples.map((example) => example.scenario?.label ?? null)).toEqual(["Clean", "Math", null]);
  expect(examples.find((example) => example.image_id === "other")?.scenario).toBeNull();
});

test("replaces unavailable and wrong-receipt ledger candidates with the next verified receipt", async () => {
  const clean = receipt("clean");
  const stale = receipt("missing-cache", ["receipt_format"]);
  const removed = receipt("removed-region", ["receipt_format"], 4);
  const replacement = receipt("current-format", ["receipt_format"]);
  mockLedger([stale, removed, replacement].map((candidate) => issue(candidate, "receipt_format")));
  fetchHealth.mockImplementation(async (_size, _seed, _offset, options) => {
    if (!options?.imageId) return response([clean]);
    if (options.imageId === stale.image_id) throw new Error("404");
    if (options.imageId === removed.image_id) return response([receipt(removed.image_id)]);
    return response([replacement]);
  });
  const examples = await loadReceiptHealthExamples();
  expect(examples.find((example) => example.scenario?.id === "format")?.image_id).toBe("current-format");
  expect(examples.map((example) => example.image_id)).not.toContain("removed-region");
  expect(fetchHealth.mock.calls.map((call) => call[3]?.imageId)).toEqual([
    undefined, "missing-cache", "removed-region", "current-format",
  ]);
});

test("rejects a stale classification when the cached check now passes", async () => {
  const stale = receipt("now-passing", ["financial_math"]);
  const current = receipt("current-ocr", ["financial_math"]);
  mockLedger([stale, current].map((candidate) => issue(candidate, "financial_math", "reocr_needed")));
  fetchHealth.mockImplementation(async (_size, _seed, _offset, options) => response(
    options?.imageId === current.image_id ? [current] : [receipt("now-passing")],
  ));
  const examples = await loadReceiptHealthExamples();
  expect(examples.find((example) => example.scenario?.id === "ocr")?.image_id).toBe("current-ocr");
  expect(examples.find((example) => example.image_id === "now-passing")?.scenario?.id).toBe("clean");
  expect(fetchHealth.mock.calls.filter((call) => call[3]?.imageId === "current-ocr")).toHaveLength(1);
});

test("bounds failed candidate requests and keeps available general examples", async () => {
  const general = [receipt("clean"), receipt("other")];
  mockLedger(Array.from({ length: 6 }, (_, index) => issue(
    receipt(`stale-${index}`, ["receipt_format"]), "receipt_format",
  )));
  fetchHealth.mockImplementation(async (_size, _seed, _offset, options) => {
    if (options?.imageId) throw new Error("404");
    return response(general);
  });
  const examples = await loadReceiptHealthExamples();
  expect(examples.map((example) => example.image_id)).toEqual(["clean", "other"]);
  expect(examples.some((example) => example.scenario?.id === "format")).toBe(false);
  expect(fetchHealth).toHaveBeenCalledTimes(4);
});

test("a missing ledger preserves the batch, and a missing batch can recover from the ledger", async () => {
  fetchHealth.mockResolvedValue(response([receipt("clean")]));
  fetchIssues.mockRejectedValue(new Error("ledger unavailable"));
  expect((await loadReceiptHealthExamples())[0].image_id).toBe("clean");
  const format = receipt("format", ["receipt_format"]);
  mockLedger([issue(format, "receipt_format")]);
  fetchHealth.mockImplementation(async (_size, _seed, _offset, options) => {
    if (!options?.imageId) throw new Error("batch unavailable");
    return response([format]);
  });
  const examples = await loadReceiptHealthExamples();
  expect(examples[0].scenario?.id).toBe("format");
  expect(preferredCheckIdForReceipt(examples[0])).toBe("receipt_format");
});

test("an empty cache stays empty and a total outage propagates the load error", async () => {
  fetchHealth.mockResolvedValue(response([]));
  expect(await loadReceiptHealthExamples()).toEqual([]);
  fetchHealth.mockRejectedValue(new Error("cache unavailable"));
  fetchIssues.mockRejectedValue(new Error("ledger unavailable"));
  await expect(loadReceiptHealthExamples()).rejects.toThrow("cache unavailable");
});

test("stalled optional discovery returns the usable general batch and clears timers", async () => {
  jest.useFakeTimers();
  try {
    fetchHealth.mockResolvedValue(response([receipt("clean")]));
    fetchIssues.mockImplementation(() => new Promise(() => {}));
    const result = loadReceiptHealthExamples();
    await jest.advanceTimersByTimeAsync(3000);
    expect((await result).map((example) => example.image_id)).toEqual(["clean"]);
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("stalled candidates cannot extend discovery past the overall load deadline", async () => {
  jest.useFakeTimers();
  try {
    mockLedger(Array.from({ length: 3 }, (_, index) => issue(
      receipt(`slow-${index}`, ["receipt_format"]), "receipt_format",
    )));
    fetchHealth.mockImplementation((_size, _seed, _offset, options) =>
      options?.imageId ? new Promise(() => {}) : Promise.resolve(response([receipt("clean")])),
    );
    const result = loadReceiptHealthExamples();
    await jest.advanceTimersByTimeAsync(8000);
    expect((await result).map((example) => example.image_id)).toEqual(["clean"]);
    expect(fetchHealth).toHaveBeenCalledTimes(4);
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("a stalled general batch can recover from verified ledger candidates", async () => {
  jest.useFakeTimers();
  try {
    const format = receipt("format", ["receipt_format"]);
    mockLedger([issue(format, "receipt_format")]);
    fetchHealth.mockImplementation((_size, _seed, _offset, options) =>
      options?.imageId ? Promise.resolve(response([format])) : new Promise(() => {}),
    );
    const result = loadReceiptHealthExamples();
    await jest.advanceTimersByTimeAsync(3000);
    expect((await result)[0].scenario?.id).toBe("format");
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("rendered replacement examples retain initial and selected focus under Strict Mode", async () => {
  jest.useFakeTimers();
  const imageSpy = jest.spyOn(window, "Image").mockImplementation(() => {
    const image = document.createElement("img");
    Object.defineProperty(image, "src", { set: () => {
      Promise.resolve().then(() => image.dispatchEvent(new Event("load")));
    } });
    return image;
  });
  try {
    const format = receipt("replacement-format", ["receipt_format"]);
    const math = receipt("replacement-math", ["financial_math"]);
    fetchHealth.mockResolvedValue(response([format, math]));
    mockLedger([issue(format, "receipt_format"), issue(math, "financial_math")]);
    render(<StrictMode><ReceiptHealthExplorer /></StrictMode>);
    await waitFor(() => expect(screen.getByRole("button", { name: "Format INVALID" })).toHaveAttribute("aria-pressed", "true"));
    expect(fetchHealth).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("Details"));
    fireEvent.click(screen.getByRole("button", { name: "Math" }));
    await act(async () => { await jest.advanceTimersByTimeAsync(650); });
    expect(screen.getByRole("button", { name: "Math INVALID" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Math" })).toHaveAttribute("aria-pressed", "true");
  } finally {
    imageSpy.mockRestore();
    jest.useRealTimers();
  }
});
