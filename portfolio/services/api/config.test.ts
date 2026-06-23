describe('API_CONFIG', () => {
  const originalEnv = process.env.NEXT_PUBLIC_API_URL;
  const originalNodeEnv = process.env.NODE_ENV;

  const setNodeEnv = (value: string | undefined) => {
    if (value === undefined) {
      Reflect.deleteProperty(process.env, 'NODE_ENV');
      return;
    }
    Object.defineProperty(process.env, 'NODE_ENV', {
      value,
      configurable: true,
      writable: true,
    });
  };

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.NEXT_PUBLIC_API_URL;
    } else {
      process.env.NEXT_PUBLIC_API_URL = originalEnv;
    }
    setNodeEnv(originalNodeEnv);
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

  test('uses local proxy in development when NEXT_PUBLIC_API_URL is not set', () => {
    setNodeEnv('development');
    delete process.env.NEXT_PUBLIC_API_URL;
    const { API_CONFIG } = require('./config');
    expect(API_CONFIG.baseUrl).toBe('/api');
  });

  test('uses configured API URL in development when set', () => {
    setNodeEnv('development');
    process.env.NEXT_PUBLIC_API_URL = 'http://127.0.0.1:3333';
    const { API_CONFIG } = require('./config');
    expect(API_CONFIG.baseUrl).toBe('http://127.0.0.1:3333');
  });
});
