export type Handler = { url: string; resolver: () => any };

export const rest = {
  get: (url: string, resolver: Handler["resolver"]) => ({ url, resolver }),
};

export const setupServer = (...handlers: Handler[]) => {
  let fetchMock: jest.Mock;
  return {
    listen: () => {
      fetchMock = jest.fn(async (input: RequestInfo) => {
        const url = typeof input === "string" ? input : input.toString();
        const handler = handlers.find(h => url.startsWith(h.url));
        if (handler) {
          const result = await handler.resolver();
          return {
            ok: true,
            status: 200,
            json: async () => result,
          } as Response;
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      });
      (global as any).fetch = fetchMock;
    },
    resetHandlers: () => {
      if (fetchMock) fetchMock.mockReset();
    },
    close: () => {
      if (fetchMock) fetchMock.mockReset();
    },
  };
};
