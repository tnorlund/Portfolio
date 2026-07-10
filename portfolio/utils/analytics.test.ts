type AnalyticsModule = typeof import("./analytics");

const originalEnv = process.env;
const originalCrypto = window.crypto;
const originalImage = window.Image;
const originalReferrerDescriptor = Object.getOwnPropertyDescriptor(
  document,
  "referrer"
);

type MockImageInstance = {
  decoding: string;
  src: string;
};

function loadAnalytics(env: Record<string, string | undefined> = {}) {
  jest.resetModules();
  process.env = {
    ...originalEnv,
    NEXT_PUBLIC_GA_MEASUREMENT_ID: env.NEXT_PUBLIC_GA_MEASUREMENT_ID,
    NEXT_PUBLIC_GTM_ID: env.NEXT_PUBLIC_GTM_ID,
    NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH:
      env.NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH,
  };

  return require("./analytics") as AnalyticsModule;
}

function mockCryptoIds(...ids: string[]): jest.Mock {
  const randomUUID = jest.fn();

  ids.forEach((id) => {
    randomUUID.mockReturnValueOnce(id);
  });
  randomUUID.mockReturnValue("fallback-id");

  Object.defineProperty(window, "crypto", {
    configurable: true,
    value: { randomUUID },
  });

  return randomUUID;
}

function setDocumentReferrer(value: string): void {
  Object.defineProperty(document, "referrer", {
    configurable: true,
    value,
  });
}

function mockImageRequests(): MockImageInstance[] {
  const instances: MockImageInstance[] = [];

  Object.defineProperty(window, "Image", {
    configurable: true,
    value: jest.fn(() => {
      const image: MockImageInstance = {
        decoding: "",
        src: "",
      };
      instances.push(image);
      return image;
    }),
  });

  return instances;
}

async function flushBeaconPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

function setViewport({
  path = "/receipt",
  search = "",
  scrollHeight = 4000,
  innerHeight = 1000,
  scrollY = 0,
}: {
  path?: string;
  search?: string;
  scrollHeight?: number;
  innerHeight?: number;
  scrollY?: number;
} = {}): void {
  window.history.pushState({}, "", `${path}${search}`);
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: innerHeight,
  });
  Object.defineProperty(window, "scrollY", {
    configurable: true,
    value: scrollY,
  });
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
  Object.defineProperty(document.body, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
}

describe("analytics utilities", () => {
  beforeEach(() => {
    process.env = { ...originalEnv };
    window.sessionStorage.clear();
    window.dataLayer = [];
    window.gtag = jest.fn();
    window.fetch = jest.fn().mockResolvedValue({ ok: true }) as jest.Mock;
    setDocumentReferrer("");
    jest.spyOn(Date, "now").mockReturnValue(1781889000000);
    mockCryptoIds("session-id", "event-id", "second-event-id");
    setViewport();
  });

  afterEach(() => {
    jest.restoreAllMocks();
    jest.resetModules();
    process.env = originalEnv;
    Object.defineProperty(window, "crypto", {
      configurable: true,
      value: originalCrypto,
    });
    Object.defineProperty(window, "Image", {
      configurable: true,
      value: originalImage,
      writable: true,
    });
    if (originalReferrerDescriptor) {
      Object.defineProperty(
        document,
        "referrer",
        originalReferrerDescriptor
      );
    } else {
      delete (document as unknown as { referrer?: string }).referrer;
    }
    delete window.gtag;
    delete window.dataLayer;
  });

  test("trackEvent enriches GA, dataLayer, and CloudFront beacon events", () => {
    const analytics = loadAnalytics({
      NEXT_PUBLIC_GA_MEASUREMENT_ID: "G-TEST",
    });

    const meta = analytics.trackEvent("reader_summary", {
      page_path: "/receipt",
      quick_jump: false,
      screens_per_minute: 2.345,
      metric_value: Number.POSITIVE_INFINITY,
      omitted: undefined,
    });

    expect(meta).toEqual({
      sessionId: "ses_session-id",
      eventId: "evt_event-id",
    });
    expect(window.dataLayer).toEqual([
      expect.objectContaining({
        event: "reader_summary",
        analytics_session_id: "ses_session-id",
        analytics_event_id: "evt_event-id",
        quick_jump: false,
      }),
    ]);
    expect(window.gtag).toHaveBeenCalledWith(
      "event",
      "reader_summary",
      expect.objectContaining({
        analytics_session_id: "ses_session-id",
        analytics_event_id: "evt_event-id",
      })
    );

    const beaconUrl = new URL((window.fetch as jest.Mock).mock.calls[0][0]);
    expect(beaconUrl.pathname).toBe("/analytics/pixel.txt");
    expect(beaconUrl.searchParams.get("event")).toBe("reader_summary");
    expect(beaconUrl.searchParams.get("sid")).toBe("ses_session-id");
    expect(beaconUrl.searchParams.get("eid")).toBe("evt_event-id");
    expect(beaconUrl.searchParams.get("page_path")).toBe("/receipt");
    expect(beaconUrl.searchParams.get("quick_jump")).toBe("false");
    expect(beaconUrl.searchParams.has("metric_value")).toBe(false);
    expect(beaconUrl.searchParams.has("omitted")).toBe(false);
    expect(beaconUrl.searchParams.has("utm_source")).toBe(false);
    expect(beaconUrl.searchParams.has("utm_medium")).toBe(false);
    expect(beaconUrl.searchParams.has("utm_campaign")).toBe(false);
    expect(beaconUrl.searchParams.has("ref")).toBe(false);
  });

  test("CloudFront beacons capture UTM attribution and referrer", () => {
    const imageRequests = mockImageRequests();
    const longReferrerPath = `/in/${"recruiter-portfolio-".repeat(10)}`;
    setViewport({
      search:
        "?utm_source=li&utm_medium=dm&utm_campaign=arthur-babylist",
    });
    setDocumentReferrer(
      `https://www.linkedin.com${longReferrerPath}?token=private#message`
    );
    const analytics = loadAnalytics();

    analytics.trackEvent("scroll_depth", {
      page_path: "/receipt",
      percent_scrolled: 25,
    });

    const beaconUrl = new URL((window.fetch as jest.Mock).mock.calls[0][0]);
    expect(beaconUrl.pathname).toBe("/analytics/pixel.txt");
    expect(beaconUrl.searchParams.get("eid")).toBe("evt_event-id");
    expect(beaconUrl.searchParams.get("path")).toBe("/receipt");
    expect(beaconUrl.searchParams.get("utm_source")).toBe("li");
    expect(beaconUrl.searchParams.get("utm_medium")).toBe("dm");
    expect(beaconUrl.searchParams.get("utm_campaign")).toBe(
      "arthur-babylist"
    );
    expect(beaconUrl.searchParams.get("ref")).toBe(
      `https://www.linkedin.com${longReferrerPath}`
    );
    expect(beaconUrl.searchParams.get("ref")).not.toContain("token");
    expect(beaconUrl.searchParams.get("ref")?.length).toBeGreaterThan(120);
    expect(window.fetch).toHaveBeenCalledTimes(1);
    expect(imageRequests).toHaveLength(0);
    expect(window.dataLayer?.[0]).not.toHaveProperty("utm_campaign");
    expect(window.dataLayer?.[0]).not.toHaveProperty("ref");
  });

  test("trackPageView increments the anonymous session page count", () => {
    const analytics = loadAnalytics();

    analytics.trackPageView("/receipt?source=test");
    analytics.trackPageView("/resume");

    expect(analytics.getAnalyticsSessionPageViews()).toBe(2);
    expect(window.dataLayer).toEqual([
      expect.objectContaining({
        event: "page_view",
        page_path: "/receipt?source=test",
        session_page_views: 1,
      }),
      expect.objectContaining({
        event: "page_view",
        page_path: "/resume",
        session_page_views: 2,
      }),
    ]);
  });

  test("initializeScrollDepthTracking emits each threshold only once per page", () => {
    const analytics = loadAnalytics();
    const cleanup = analytics.initializeScrollDepthTracking();

    Object.defineProperty(window, "scrollY", {
      configurable: true,
      value: 2600,
    });
    window.dispatchEvent(new Event("scroll"));
    window.dispatchEvent(new Event("scroll"));

    const scrollEvents = (window.dataLayer ?? []).filter(
      (entry) =>
        typeof entry === "object" &&
        entry !== null &&
        "event" in entry &&
        entry.event === "scroll_depth"
    );

    expect(scrollEvents).toHaveLength(4);
    expect(scrollEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ percent_scrolled: 25 }),
        expect.objectContaining({ percent_scrolled: 50 }),
        expect.objectContaining({ percent_scrolled: 75 }),
        expect.objectContaining({ percent_scrolled: 90 }),
      ])
    );

    cleanup();
  });

  test("trackWebVital normalizes CLS and non-CLS values", () => {
    const analytics = loadAnalytics();

    analytics.trackWebVital({
      name: "CLS",
      value: 0.1234,
      delta: 0.1,
      id: "vital-1",
    });
    analytics.trackWebVital({
      name: "LCP",
      value: 2399.6,
      delta: 100,
      id: "vital-2",
      navigationType: "reload",
    });

    expect(window.dataLayer).toEqual([
      expect.objectContaining({
        event: "web_vital",
        metric_name: "CLS",
        value: 123,
      }),
      expect.objectContaining({
        event: "web_vital",
        metric_name: "LCP",
        metric_navigation_type: "reload",
        value: 2400,
      }),
    ]);
  });

  test("disabled CloudFront beacon path skips fetch but keeps dataLayer", () => {
    const analytics = loadAnalytics({
      NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH: "disabled",
    });

    analytics.trackEvent("page_view", { page_path: "/receipt" });

    expect(window.dataLayer).toHaveLength(1);
    expect(window.fetch).not.toHaveBeenCalled();
  });

  test("external CloudFront beacon path is ignored", () => {
    const analytics = loadAnalytics({
      NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH:
        "https://example.com/analytics/pixel.txt",
    });

    analytics.trackEvent("page_view", { page_path: "/receipt" });

    expect(window.dataLayer).toHaveLength(1);
    expect(window.fetch).not.toHaveBeenCalled();
  });

  test("relative CloudFront beacon path stays same-origin", () => {
    const analytics = loadAnalytics({
      NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH:
        "/custom/pixel.txt?source=analytics",
    });

    analytics.trackEvent("page_view", { page_path: "/receipt" });

    const beaconUrl = new URL((window.fetch as jest.Mock).mock.calls[0][0]);
    expect(beaconUrl.origin).toBe(window.location.origin);
    expect(beaconUrl.pathname).toBe("/custom/pixel.txt");
    expect(beaconUrl.searchParams.get("source")).toBe("analytics");
    expect(beaconUrl.searchParams.get("eid")).toBe("evt_event-id");
    expect(window.fetch).toHaveBeenCalledTimes(1);
  });

  test("fetch failures retry the same beacon once via Image", async () => {
    const imageRequests = mockImageRequests();
    (window.fetch as jest.Mock).mockRejectedValueOnce(
      new TypeError("collector unavailable")
    );
    setViewport({
      search:
        "?utm_source=li&utm_medium=dm&utm_campaign=arthur-babylist",
    });
    setDocumentReferrer("https://www.linkedin.com/?token=private");
    const analytics = loadAnalytics();

    analytics.trackEvent("page_view", { page_path: "/receipt" });
    await flushBeaconPromises();

    expect(window.fetch).toHaveBeenCalledTimes(1);
    expect(imageRequests).toHaveLength(1);
    const fetchUrl = new URL(
      (window.fetch as jest.Mock).mock.calls[0][0]
    );
    const imageUrl = new URL(imageRequests[0].src);
    expect(fetchUrl.pathname).toBe("/analytics/pixel.txt");
    expect(imageUrl.toString()).toBe(fetchUrl.toString());
    expect(imageUrl.searchParams.get("event")).toBe("page_view");
    expect(imageUrl.searchParams.get("sid")).toBe("ses_session-id");
    expect(imageUrl.searchParams.get("eid")).toBe("evt_event-id");
    expect(imageUrl.searchParams.get("utm_campaign")).toBe(
      "arthur-babylist"
    );
    expect(imageUrl.searchParams.get("ref")).toBe(
      "https://www.linkedin.com/"
    );
  });
});
