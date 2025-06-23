describe('API_CONFIG', () => {
  const originalEnv = process.env.NEXT_PUBLIC_API_URL;

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.NEXT_PUBLIC_API_URL;
    } else {
      process.env.NEXT_PUBLIC_API_URL = originalEnv;
    }
    jest.resetModules();
  });

  test('reflects NEXT_PUBLIC_API_URL when set', () => {
    process.env.NEXT_PUBLIC_API_URL = 'https://example.com';
    const { API_CONFIG } = require('./config');
    expect(API_CONFIG.baseUrl).toBe('https://example.com');
  });

  test('falls back to default when NEXT_PUBLIC_API_URL not set', () => {
    delete process.env.NEXT_PUBLIC_API_URL;
    const { API_CONFIG } = require('./config');
    expect(API_CONFIG.baseUrl).toBe('https://api.tylernorlund.com');
  });
});
