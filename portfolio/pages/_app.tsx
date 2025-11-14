import type { AppProps } from "next/app";
import Link from "next/link";
import React, { Suspense, useEffect } from "react";
import dynamic from "next/dynamic";
import "../styles/globals.css";
import { PerformanceProvider } from "../components/providers/PerformanceProvider";

// Dynamically import the performance overlay to avoid SSR issues
const PerformanceOverlay = dynamic(
  () => import("../components/dev/PerformanceOverlay"),
  { ssr: false }
);

// Error boundary component
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("Error caught by boundary:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: "20px", textAlign: "center" }}>
          <h2>Something went wrong</h2>
          <details style={{ whiteSpace: "pre-wrap" }}>
            {this.state.error && this.state.error.toString()}
          </details>
        </div>
      );
    }

    return this.props.children;
  }
}

export default function App({ Component, pageProps }: AppProps) {
  useEffect(() => {
    // Report Web Vitals for Real User Monitoring
    if (typeof window !== 'undefined') {
      import('web-vitals').then(({ onCLS, onINP, onFCP, onLCP, onTTFB }) => {
        const reportWebVitals = (metric: any) => {
          // Log to console in development
          if (process.env.NODE_ENV === 'development') {
            console.log(metric);
          }
          
          // Send to analytics endpoint in production
          if (process.env.NODE_ENV === 'production') {
            const body = JSON.stringify({
              name: metric.name,
              value: metric.value,
              delta: metric.delta,
              id: metric.id,
              navigationType: metric.navigationType,
              url: window.location.href,
              timestamp: new Date().toISOString(),
            });
            
            // Use sendBeacon if available for reliability
            if (navigator.sendBeacon) {
              navigator.sendBeacon('/api/analytics', body);
            } else {
              // Fallback to fetch
              fetch('/api/analytics', {
                method: 'POST',
                body,
                headers: { 'Content-Type': 'application/json' },
                keepalive: true,
              }).catch(() => {
                // Silently fail - don't impact user experience
              });
            }
          }
        };
        
        onCLS(reportWebVitals);
        onINP(reportWebVitals); // INP replaces FID in web-vitals v5
        onFCP(reportWebVitals);
        onLCP(reportWebVitals);
        onTTFB(reportWebVitals);
      });
    }
  }, []);

  return (
    <ErrorBoundary>
      <PerformanceProvider>
        <Suspense fallback={<div>Loading...</div>}>
          <div>
            <header>
              <h1>
                <Link href="/">Tyler Norlund</Link>
              </h1>
            </header>
            <Component {...pageProps} />
            {process.env.NODE_ENV === "development" && <PerformanceOverlay />}
          </div>
        </Suspense>
      </PerformanceProvider>
    </ErrorBoundary>
  );
}
