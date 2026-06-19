export const GA_MEASUREMENT_ID =
  process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim();
export const GTM_ID = process.env.NEXT_PUBLIC_GTM_ID?.trim();

const SCROLL_THRESHOLDS = [25, 50, 75, 90];

type AnalyticsParams = Record<
  string,
  string | number | boolean | undefined
>;

type WebVitalMetric = {
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

function getPagePath(): string {
  return `${window.location.pathname}${window.location.search}`;
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

export function trackEvent(
  event: string,
  params: AnalyticsParams = {}
): void {
  if (typeof window === "undefined") {
    return;
  }

  pushDataLayerEvent(event, params);

  if (GA_MEASUREMENT_ID && typeof window.gtag === "function") {
    window.gtag("event", event, params);
  }
}

export function trackPageView(url: string): void {
  if (typeof window === "undefined") {
    return;
  }

  const pageLocation = new URL(url, window.location.origin).toString();

  pushDataLayerEvent("page_view", {
    page_path: url,
    page_location: pageLocation,
  });

  if (GA_MEASUREMENT_ID && typeof window.gtag === "function") {
    window.gtag("config", GA_MEASUREMENT_ID, {
      page_path: url,
      page_location: pageLocation,
    });
  }
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
