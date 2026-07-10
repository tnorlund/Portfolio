const DEFAULT_CLOUDFRONT_ANALYTICS_BEACON_PATH = "/analytics/pixel.txt";

function getPublicAnalyticsId(
  value: string | undefined,
  pattern: RegExp
): string | undefined {
  const id = value?.trim();
  return id && pattern.test(id) ? id : undefined;
}

function getCloudFrontBeaconPath(value: string | undefined): string {
  const path = value?.trim();

  if (!path) {
    return DEFAULT_CLOUDFRONT_ANALYTICS_BEACON_PATH;
  }

  if (path === "disabled") {
    return "disabled";
  }

  if (!path.startsWith("/") || path.startsWith("//")) {
    return "disabled";
  }

  try {
    const url = new URL(path, "https://tylernorlund.com");
    return `${url.pathname}${url.search}`;
  } catch {
    return "disabled";
  }
}

export const GA_MEASUREMENT_ID = getPublicAnalyticsId(
  process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID,
  /^G-[A-Z0-9-]+$/
);
export const GTM_ID = getPublicAnalyticsId(
  process.env.NEXT_PUBLIC_GTM_ID,
  /^GTM-[A-Z0-9]+$/
);
export const CLOUDFRONT_ANALYTICS_BEACON_PATH = getCloudFrontBeaconPath(
  process.env.NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH
);

const SCROLL_THRESHOLDS = [25, 50, 75, 90];
const ANALYTICS_SESSION_ID_KEY = "tnor.analyticsSessionId";
const ANALYTICS_PAGE_VIEW_COUNT_KEY = "tnor.analyticsPageViews";
const BEACON_UTM_PARAM_KEYS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
] as const;

const CLOUDFRONT_BEACON_PARAM_KEYS = [
  "page_path",
  "percent_scrolled",
  "metric_name",
  "metric_value",
  "time_to_bottom_ms",
  "active_scroll_ms",
  "page_height",
  "scrollable_pixels",
  "screens_per_minute",
  "reader_delta_percent",
  "baseline_sample_size",
  "session_page_views",
  "quick_jump",
  ...BEACON_UTM_PARAM_KEYS,
  "ref",
];

export type AnalyticsParams = Record<
  string,
  string | number | boolean | undefined
>;

export type AnalyticsEventMeta = {
  sessionId: string;
  eventId: string;
};

export type WebVitalMetric = {
  name: string;
  value: number;
  delta: number;
  id: string;
  navigationType?: string;
};

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

function getSessionStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function getBeaconAttributionParams(): AnalyticsParams {
  const attribution: AnalyticsParams = {};
  const searchParams = new URLSearchParams(window.location.search);

  BEACON_UTM_PARAM_KEYS.forEach((key) => {
    const value = searchParams.get(key);

    if (value) {
      attribution[key] = value;
    }
  });

  if (document.referrer) {
    attribution.ref = document.referrer;
  }

  return attribution;
}

function createAnalyticsId(prefix: string): string {
  const cryptoApi = window.crypto;

  if (cryptoApi?.randomUUID) {
    return `${prefix}_${cryptoApi.randomUUID()}`;
  }

  if (cryptoApi?.getRandomValues) {
    const values = new Uint32Array(2);
    cryptoApi.getRandomValues(values);
    return `${prefix}_${values[0].toString(36)}${values[1].toString(36)}`;
  }

  return `${prefix}_${Date.now().toString(36)}${Math.random()
    .toString(36)
    .slice(2)}`;
}

function cleanParams(params: AnalyticsParams): AnalyticsParams {
  const clean: AnalyticsParams = {};

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) {
      clean[key] = value;
    }
  });

  return clean;
}

export function getAnalyticsSessionId(): string {
  if (typeof window === "undefined") {
    return "";
  }

  const storage = getSessionStorage();
  const existingSessionId = storage?.getItem(ANALYTICS_SESSION_ID_KEY);

  if (existingSessionId) {
    return existingSessionId;
  }

  const sessionId = createAnalyticsId("ses");
  storage?.setItem(ANALYTICS_SESSION_ID_KEY, sessionId);
  return sessionId;
}

export function getAnalyticsSessionPageViews(): number {
  if (typeof window === "undefined") {
    return 0;
  }

  const value = getSessionStorage()?.getItem(
    ANALYTICS_PAGE_VIEW_COUNT_KEY
  );
  const parsed = Number(value);

  return Number.isFinite(parsed) ? parsed : 0;
}

function incrementAnalyticsSessionPageViews(): number {
  const nextPageViews = getAnalyticsSessionPageViews() + 1;
  getSessionStorage()?.setItem(
    ANALYTICS_PAGE_VIEW_COUNT_KEY,
    String(nextPageViews)
  );
  return nextPageViews;
}

function getPagePath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

function getBeaconPagePath(): string {
  return window.location.pathname || "/";
}

function pushDataLayerEvent(
  event: string,
  params: AnalyticsParams = {}
): void {
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push({
    event,
    ...params,
  });
}

function appendBeaconParam(
  url: URL,
  key: string,
  value: string | number | boolean | undefined
): void {
  if (value === undefined) {
    return;
  }

  if (typeof value === "number" && !Number.isFinite(value)) {
    return;
  }

  const stringValue = String(value);

  if (!stringValue) {
    return;
  }

  url.searchParams.set(key, stringValue.slice(0, 120));
}

function getStaticBeaconMirrorUrl(url: URL): URL | null {
  const mirrorUrl = new URL(
    DEFAULT_CLOUDFRONT_ANALYTICS_BEACON_PATH,
    window.location.origin
  );

  if (
    mirrorUrl.origin === url.origin &&
    mirrorUrl.pathname === url.pathname
  ) {
    return null;
  }

  mirrorUrl.search = url.search;
  mirrorUrl.searchParams.delete("live_eid");
  return mirrorUrl;
}

function sendImageBeacon(url: URL): void {
  try {
    const image = new Image();
    image.decoding = "async";
    image.src = url.toString();
  } catch {
    // Analytics should never affect the page experience.
  }
}

function sendCloudFrontBeacon(
  event: string,
  params: AnalyticsParams,
  meta: AnalyticsEventMeta
): void {
  if (
    !CLOUDFRONT_ANALYTICS_BEACON_PATH ||
    CLOUDFRONT_ANALYTICS_BEACON_PATH === "disabled"
  ) {
    return;
  }

  try {
    const url = new URL(
      CLOUDFRONT_ANALYTICS_BEACON_PATH,
      window.location.origin
    );

    if (url.origin !== window.location.origin) {
      return;
    }

    appendBeaconParam(url, "v", 1);
    appendBeaconParam(url, "event", event);
    appendBeaconParam(url, "sid", meta.sessionId);
    appendBeaconParam(url, "eid", meta.eventId);
    appendBeaconParam(url, "path", getBeaconPagePath());
    appendBeaconParam(url, "ts", Date.now());

    const beaconParams = {
      ...params,
      ...getBeaconAttributionParams(),
    };

    CLOUDFRONT_BEACON_PARAM_KEYS.forEach((key) => {
      appendBeaconParam(url, key, beaconParams[key]);
    });

    const mirrorUrl = getStaticBeaconMirrorUrl(url);
    if (mirrorUrl) {
      url.searchParams.delete("eid");
      appendBeaconParam(url, "live_eid", meta.eventId);
      sendImageBeacon(mirrorUrl);
    }

    if (typeof window.fetch === "function") {
      window
        .fetch(url.toString(), {
          method: "GET",
          keepalive: true,
          cache: "no-store",
        })
        .catch(() => {
          // The static mirror already preserves the durable event.
        });
      return;
    }

    sendImageBeacon(url);
  } catch {
    // Analytics should never affect the page experience.
  }
}

export function trackEvent(
  event: string,
  params: AnalyticsParams = {}
): AnalyticsEventMeta {
  const emptyMeta = { sessionId: "", eventId: "" };

  if (typeof window === "undefined") {
    return emptyMeta;
  }

  const meta = {
    sessionId: getAnalyticsSessionId(),
    eventId: createAnalyticsId("evt"),
  };
  const enrichedParams = cleanParams({
    ...params,
    analytics_session_id: meta.sessionId,
    analytics_event_id: meta.eventId,
  });

  pushDataLayerEvent(event, enrichedParams);

  if (GA_MEASUREMENT_ID && typeof window.gtag === "function") {
    window.gtag("event", event, enrichedParams);
  }

  sendCloudFrontBeacon(event, enrichedParams, meta);

  return meta;
}

export function trackPageView(url: string): void {
  if (typeof window === "undefined") {
    return;
  }

  const pageLocation = new URL(url, window.location.origin).toString();
  const sessionPageViews = incrementAnalyticsSessionPageViews();

  trackEvent("page_view", {
    page_path: url,
    page_location: pageLocation,
    session_page_views: sessionPageViews,
  });
}

export function trackWebVital(metric: WebVitalMetric): void {
  if (typeof window === "undefined") {
    return;
  }

  const value =
    metric.name === "CLS"
      ? Math.round(metric.value * 1000)
      : Math.round(metric.value);

  trackEvent("web_vital", {
    metric_name: metric.name,
    metric_id: metric.id,
    metric_value: metric.value,
    metric_delta: metric.delta,
    metric_navigation_type: metric.navigationType,
    page_path: getPagePath(),
    value,
    non_interaction: true,
  });
}

export function initializeScrollDepthTracking(): () => void {
  if (typeof window === "undefined") {
    return () => {};
  }

  const firedByPage = new Map<string, Set<number>>();

  const handleScroll = () => {
    const documentElement = document.documentElement;
    const body = document.body;
    const scrollHeight = Math.max(
      documentElement.scrollHeight,
      body?.scrollHeight ?? 0
    );

    if (scrollHeight <= window.innerHeight) {
      return;
    }

    const percentScrolled = Math.min(
      100,
      Math.round(
        ((window.scrollY + window.innerHeight) / scrollHeight) * 100
      )
    );
    const pagePath = getPagePath();
    const firedThresholds = firedByPage.get(pagePath) ?? new Set<number>();

    for (const threshold of SCROLL_THRESHOLDS) {
      if (
        percentScrolled >= threshold &&
        !firedThresholds.has(threshold)
      ) {
        firedThresholds.add(threshold);
        trackEvent("scroll_depth", {
          page_path: pagePath,
          percent_scrolled: threshold,
          page_height: scrollHeight,
          viewport_height: window.innerHeight,
        });
      }
    }

    firedByPage.set(pagePath, firedThresholds);
  };

  window.addEventListener("scroll", handleScroll, { passive: true });
  window.addEventListener("resize", handleScroll);
  handleScroll();

  return () => {
    window.removeEventListener("scroll", handleScroll);
    window.removeEventListener("resize", handleScroll);
  };
}
