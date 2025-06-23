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

  test('fetchReceiptCount uses dev url in development', async () => {
    const originalEnv = process.env.NODE_ENV;
    (process.env as any).NODE_ENV = 'development';
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve(8) });
    const result = await api.fetchReceiptCount();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://dev-api.tylernorlund.com/receipt_count',
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
});
