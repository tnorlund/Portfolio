import fs from 'fs';
import path from 'path';
import { api } from '../services/api';

global.fetch = jest.fn();

describe('fetchReceipts', () => {
  afterEach(() => {
    (global.fetch as jest.Mock).mockReset();
  });

  test('returns expected data', async () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'receipts.json');
    const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf-8'));
    (global.fetch as jest.Mock).mockResolvedValue({ ok: true, json: () => Promise.resolve(fixture) });
    const result = await api.fetchReceipts(1);
    expect(result).toEqual(fixture);
  });
});
