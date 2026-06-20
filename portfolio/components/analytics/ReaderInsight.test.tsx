import { act, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import ReaderInsight from "./ReaderInsight";
import { api } from "../../services/api";
import {
  getAnalyticsSessionPageViews,
  trackEvent,
} from "../../utils/analytics";

jest.mock("next/router", () => ({
  useRouter: () => ({ asPath: "/receipt?source=test" }),
}));

jest.mock("../../services/api", () => ({
  api: {
    submitReaderSummary: jest.fn(),
  },
}));

jest.mock("../../utils/analytics", () => ({
  getAnalyticsSessionPageViews: jest.fn(),
  trackEvent: jest.fn(),
}));

const mockedApi = api as jest.Mocked<typeof api>;
const mockedGetSessionPageViews =
  getAnalyticsSessionPageViews as jest.MockedFunction<
    typeof getAnalyticsSessionPageViews
  >;
const mockedTrackEvent = trackEvent as jest.MockedFunction<
  typeof trackEvent
>;

type IntersectionObserverCallback = ConstructorParameters<
  typeof IntersectionObserver
>[0];

let intersectionCallback: IntersectionObserverCallback | null = null;
let observeMock: jest.Mock;
let disconnectMock: jest.Mock;
let performanceNowMock: jest.SpyInstance<number, []>;

function setPageHeight(scrollHeight: number, innerHeight: number): void {
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: innerHeight,
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

function mockBaselineFetch(response: unknown): void {
  window.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  }) as jest.Mock;
}

async function triggerReaderIntersection(): Promise<void> {
  await waitFor(() => expect(observeMock).toHaveBeenCalled());

  await act(async () => {
    intersectionCallback?.(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver
    );
  });
}

describe("ReaderInsight", () => {
  beforeEach(() => {
    observeMock = jest.fn();
    disconnectMock = jest.fn();
    intersectionCallback = null;

    global.IntersectionObserver = jest.fn((callback) => {
      intersectionCallback = callback;
      return {
        observe: observeMock,
        disconnect: disconnectMock,
        unobserve: jest.fn(),
        takeRecords: jest.fn(() => []),
        root: null,
        rootMargin: "",
        thresholds: [0.35],
      };
    }) as unknown as typeof IntersectionObserver;

    mockedGetSessionPageViews.mockReturnValue(3);
    mockedTrackEvent.mockReturnValue({
      sessionId: "ses_reader",
      eventId: "evt_reader",
    });
    mockedApi.submitReaderSummary.mockResolvedValue({
      accepted: true,
      counted: true,
      quickJump: false,
      pagePath: "/receipt",
      minimumSampleSize: 5,
      comparison: {
        sampleSize: 12,
        averageTimeToBottomMs: 120000,
        averageActiveScrollMs: 110000,
        averageScreensPerMinute: 2.1,
        readerDeltaPercent: 50,
      },
      aggregate: {
        sampleSize: 13,
        averageTimeToBottomMs: 115000,
        averageActiveScrollMs: 108000,
        averageScreensPerMinute: 2.2,
        updatedAt: "2026-06-19T00:00:00Z",
      },
    });

    mockBaselineFetch({
      default: {
        averageTimeToBottomMs: 120000,
        sampleSize: 9,
      },
      pages: {
        "/receipt": {
          averageTimeToBottomMs: 120000,
          sampleSize: 9,
        },
      },
    });

    performanceNowMock = jest.spyOn(performance, "now");
    performanceNowMock.mockReturnValue(1000);
    setPageHeight(5200, 1000);
  });

  afterEach(() => {
    jest.clearAllMocks();
    performanceNowMock.mockRestore();
  });

  test("stays hidden when the page is not deep enough", () => {
    setPageHeight(1200, 1000);

    render(<ReaderInsight />);

    expect(screen.queryByLabelText("Reading pace")).not.toBeInTheDocument();
    expect(observeMock).not.toHaveBeenCalled();
  });

  test("submits reader timing and shows the live comparison", async () => {
    render(<ReaderInsight />);

    expect(await screen.findByLabelText("Reading pace")).toBeInTheDocument();
    expect(screen.getByText("Calculating your read.")).toBeInTheDocument();

    performanceNowMock.mockReturnValue(2000);
    act(() => {
      window.dispatchEvent(new Event("scroll"));
    });

    performanceNowMock.mockReturnValue(62000);
    await triggerReaderIntersection();

    await waitFor(() =>
      expect(mockedApi.submitReaderSummary).toHaveBeenCalledWith({
        page_path: "/receipt?source=test",
        analytics_event_id: "evt_reader",
        time_to_bottom_ms: 61000,
        active_scroll_ms: 60000,
        page_height: 5200,
        scrollable_pixels: 4200,
        screens_per_minute: 4.2,
        quick_jump: false,
      })
    );

    expect(mockedTrackEvent).toHaveBeenCalledWith(
      "reader_summary",
      expect.objectContaining({
        page_path: "/receipt?source=test",
        reader_delta_percent: 49,
        baseline_sample_size: 9,
        session_page_views: 3,
        quick_jump: false,
      })
    );
    expect(
      screen.getByText(
        "You reached the bottom 49% faster than the average reader."
      )
    ).toBeInTheDocument();
    expect(screen.queryByText("Your time")).not.toBeInTheDocument();
    expect(screen.queryByText("Active pace")).not.toBeInTheDocument();
    expect(screen.queryByText("Average sample")).not.toBeInTheDocument();
  });

  test("marks short active scrolling as a quick jump and keeps fallback UI", async () => {
    mockedApi.submitReaderSummary.mockRejectedValue(new Error("offline"));
    mockBaselineFetch({
      default: {
        sampleSize: 1,
      },
    });

    render(<ReaderInsight />);

    expect(await screen.findByLabelText("Reading pace")).toBeInTheDocument();

    performanceNowMock.mockReturnValue(3000);
    await triggerReaderIntersection();

    await waitFor(() =>
      expect(mockedApi.submitReaderSummary).toHaveBeenCalledWith(
        expect.objectContaining({
          active_scroll_ms: 2000,
          quick_jump: true,
        })
      )
    );

    expect(screen.queryByText("Quick jump")).not.toBeInTheDocument();
    expect(screen.queryByText("Collecting")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "You reached the bottom in 2s. The average will appear here after 5 previous completed reads."
      )
    ).toBeInTheDocument();
  });
});
