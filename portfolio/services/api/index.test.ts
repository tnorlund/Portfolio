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
});
