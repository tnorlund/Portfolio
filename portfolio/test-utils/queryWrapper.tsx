import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

/**
 * Wrapper for renderHook/render that provides a fresh QueryClient per test.
 * Retries are disabled so failing queries reject immediately.
 */
export const createQueryWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
    },
  });
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { Wrapper, queryClient };
};
