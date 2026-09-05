import { act, renderHook } from "@testing-library/react";
import { usePlanner } from "./usePlanner";
const reply = (value: unknown) =>
  Promise.resolve({ ok: true, json: async () => value });
const snapshot = (version: number) => ({
  version,
  items: [],
  areas: [],
  weeks: {},
  proposals: [],
  routines: [],
  content_version: version,
});
let mockFetch: jest.Mock;
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}
async function advance(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}
beforeEach(() => {
  jest.useFakeTimers();
  process.env.NEXT_PUBLIC_PLANNER_API_URL = "http://127.0.0.1:4317";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  Object.defineProperty(AbortSignal, "timeout", {
    configurable: true,
    value: () => new AbortController().signal,
  });
  Object.defineProperty(crypto, "randomUUID", {
    configurable: true,
    value: jest.fn(() => "test-request-id"),
  });
  mockFetch = jest.fn();
  global.fetch = mockFetch;
});
afterEach(() => {
  jest.useRealTimers();
  delete process.env.NEXT_PUBLIC_PLANNER_API_URL;
});
test("retries a failed refetch even after observing the new clock", async () => {
  mockFetch.mockImplementation((url: string) =>
    reply(url.endsWith("clock") ? { version: 1 } : snapshot(1)),
  );
  const { result } = renderHook(() => usePlanner());
  await settle();
  expect(result.current.data?.version).toBe(1);
  mockFetch.mockImplementation((url: string) =>
    url.endsWith("clock")
      ? reply({ version: 2 })
      : Promise.reject(new Error("Temporarily offline")),
  );
  await advance(3000);
  expect(result.current.data?.version).toBe(1);
  expect(result.current.error).toBe("Temporarily offline");
  mockFetch.mockImplementation((url: string) =>
    reply(url.endsWith("clock") ? { version: 2 } : snapshot(2)),
  );
  await advance(3000);
  expect(result.current.data?.version).toBe(2);
  expect(result.current.error).toBe("");
  const reads = mockFetch.mock.calls.filter(([url]) =>
    url.endsWith("snapshot"),
  ).length;
  await advance(3000);
  expect(
    mockFetch.mock.calls.filter(([url]) => url.endsWith("snapshot")),
  ).toHaveLength(reads);
});
test("visibility changes do not create overlapping loops and hidden tabs poll every 30 seconds", async () => {
  let finish: (value: unknown) => void = () => undefined;
  mockFetch.mockImplementation((url: string) =>
    url.endsWith("snapshot")
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : reply({ version: 1 }),
  );
  renderHook(() => usePlanner());
  await settle();
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
    finish({ ok: true, json: async () => snapshot(1) });
  });
  expect(mockFetch).toHaveBeenCalledTimes(1);
  await advance(3000);
  expect(mockFetch).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(mockFetch).toHaveBeenCalledTimes(3);
  await advance(29999);
  expect(mockFetch).toHaveBeenCalledTimes(3);
  await advance(1);
  expect(mockFetch).toHaveBeenCalledTimes(4);
});
test("reuses the operation id after an uncertain save", async () => {
  mockFetch.mockImplementation(() => reply(snapshot(1)));
  const { result } = renderHook(() => usePlanner());
  await settle();
  let attempts = 0;
  mockFetch.mockImplementation((_url: string, options: { method?: string }) => {
    if (options.method === "POST")
      return ++attempts === 1
        ? Promise.reject(new Error("Lost response"))
        : reply({ version: 2, result: {} });
    return reply(snapshot(2));
  });
  const command = { action: "save_item", text: "Write a draft" };
  await act(async () => {
    await expect(result.current.execute(command)).rejects.toThrow(
      "Lost response",
    );
  });
  await act(async () => {
    await result.current.execute(command);
  });
  const writes = mockFetch.mock.calls
    .filter(([, options]) => options.method === "POST")
    .map(([, options]) => JSON.parse(options.body));
  expect(writes).toHaveLength(2);
  expect(writes[0].request_id).toBe(writes[1].request_id);
  expect(result.current.data?.version).toBe(2);
});
