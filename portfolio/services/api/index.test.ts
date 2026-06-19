import { api } from './index';

const fetchMock = jest.fn();

global.fetch = fetchMock as any;

describe('api service', () => {
  afterEach(() => {
    fetchMock.mockReset();
  });

  test('fetchImageCount returns wrapped response', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(5) });
    const result = await api.fetchImageCount();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.tylernorlund.com/image_count',
      expect.any(Object)
    );
    expect(result).toEqual({ count: 5 });
  });

  test('submitReaderSummary posts reader timing payload', async () => {
    const response = {
      accepted: true,
      counted: true,
      quickJump: false,
      pagePath: '/receipt',
      minimumSampleSize: 5,
      comparison: {
        sampleSize: 7,
        averageTimeToBottomMs: 120000,
        readerDeltaPercent: 20,
      },
      aggregate: {
        sampleSize: 8,
        averageTimeToBottomMs: 115000,
      },
    };
    fetchMock.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });

    const payload = {
      page_path: '/receipt',
      analytics_session_id: 'ses_123',
      analytics_event_id: 'evt_456',
      time_to_bottom_ms: 90000,
      active_scroll_ms: 88000,
      page_height: 12000,
      scrollable_pixels: 10800,
      screens_per_minute: 2.3,
      quick_jump: false,
    };

    const result = await api.submitReaderSummary(payload);

    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.tylernorlund.com/reader_summary',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(payload),
        keepalive: true,
      })
    );
    expect(result).toEqual(response);
  });

  test('fetchReceipts sends params', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ receipts: [], lastEvaluatedKey: null }),
    });
    await api.fetchReceipts(2, { id: 'a' });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('limit=2'),
      expect.any(Object)
    );
    expect(fetchMock.mock.calls[0][0]).toContain(
      encodeURIComponent(JSON.stringify({ id: 'a' }))
    );
  });

  test('fetchReceiptCount uses prod url by default', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(7) });
    const result = await api.fetchReceiptCount();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.tylernorlund.com/receipt_count',
      expect.any(Object)
    );
    expect(result).toBe(7);
  });

  test('fetchReceiptCount uses proxy url in development', async () => {
    const originalEnv = process.env.NODE_ENV;
    (process.env as any).NODE_ENV = 'development';
    // Re-import to pick up new NODE_ENV (module caches the value)
    jest.resetModules();
    const { api: devApi } = require('./index');
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(8) });
    const result = await devApi.fetchReceiptCount();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/receipt_count',
      expect.any(Object)
    );
    expect(result).toBe(8);
    (process.env as any).NODE_ENV = originalEnv;
  });

  test('fetchMerchantCounts returns data', async () => {
    const data = [{ Walmart: 3 }];
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(data) });
    const result = await api.fetchMerchantCounts();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.tylernorlund.com/merchant_counts',
      expect.any(Object)
    );
    expect(result).toEqual(data);
  });

  test('fetchLabelValidationCount returns data', async () => {
    const data = { label: { valid: 2 } };
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(data) });
    const result = await api.fetchLabelValidationCount();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.tylernorlund.com/label_validation_count',
      expect.any(Object)
    );
    expect(result).toEqual(data);
  });

  test('fetchRandomImageDetails handles query params', async () => {
    const data = { image: {}, lines: [], receipts: [] };
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(data) });
    await api.fetchRandomImageDetails('receipt');
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.tylernorlund.com/random_image_details?image_type=receipt',
      expect.any(Object)
    );
  });

  test('fetchImagesByType passes parameters', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ images: [], lastEvaluatedKey: null }),
    });
    await api.fetchImagesByType('receipt', 3, { id: 'b' });
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain('image_type=receipt');
    expect(calledUrl).toContain('limit=3');
    expect(calledUrl).toContain(
      encodeURIComponent(JSON.stringify({ id: 'b' }))
    );
  });

  test('fetchReceiptHealthIssues passes classification filters', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ issues: [] }),
    });

    await api.fetchReceiptHealthIssues({
      state: 'all',
      checkId: 'financial_math',
      classification: 'evaluator_rule_gap',
      lane: 'receipt_structure_rule',
      rootCause: 'tip_gratuity_ambiguity',
      limit: 25,
    });

    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain('/label_evaluator/receipt_health_issues?');
    expect(calledUrl).toContain('state=all');
    expect(calledUrl).toContain('check_id=financial_math');
    expect(calledUrl).toContain('classification=evaluator_rule_gap');
    expect(calledUrl).toContain('lane=receipt_structure_rule');
    expect(calledUrl).toContain('root_cause=tip_gratuity_ambiguity');
    expect(calledUrl).toContain('limit=25');
  });
});
