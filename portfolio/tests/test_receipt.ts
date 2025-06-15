import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'fs';
import path from 'path';
import { api } from '../services/api';

test('fetchReceipts returns expected data', async () => {
  const fixturePath = path.join(__dirname, 'fixtures', 'receipts.json');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf-8'));

  const originalFetch = global.fetch;
  global.fetch = async (url: string) => {
    return {
      ok: true,
      json: async () => fixture,
    } as any;
  };

  const result = await api.fetchReceipts(1);
  assert.deepEqual(result, fixture);

  global.fetch = originalFetch;
});
