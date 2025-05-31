import React, { useEffect, useState } from "react";
import { useInView } from "react-intersection-observer";
import { ImageCountApiResponse } from "../../../types/api";
import { api } from "../../../services/api";
import dynamic from "next/dynamic";

// Create completely client-side only components
const ImageCounts = () => {
  const [mounted, setMounted] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [animatedCount, setAnimatedCount] = useState<number>(0);
  const { ref, inView } = useInView({
    triggerOnce: true,
    threshold: 0,
  });

  useEffect(() => {
    console.log("ImageCounts component mounted");
    setMounted(true);
  }, []);

  useEffect(() => {
    console.log("ImageCounts state:", {
      mounted,
      inView,
      loading,
      count,
      error,
    });
    if (mounted && inView && !loading && count === null && !error) {
      console.log("ImageCounts - Starting fetch...");
      setLoading(true);
      api
        .fetchImageCount()
        .then((fetchedCount: ImageCountApiResponse) => {
          console.log("ImageCounts - Success:", fetchedCount);
          setCount(fetchedCount.count);
          setError(null);
          // Start simple counter animation
          let current = 0;
          const target = fetchedCount.count;
          const increment = Math.ceil(target / 50); // 50 steps
          const timer = setInterval(() => {
            current += increment;
            if (current >= target) {
              setAnimatedCount(target);
              clearInterval(timer);
            } else {
              setAnimatedCount(current);
            }
          }, 20);
        })
        .catch((error: Error) => {
          console.error("ImageCounts - Error:", error);
          setError(error.message || "Failed to load image count");
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [mounted, inView, loading, count, error]);

  // Always render the container with ref attached
  return (
    <div
      ref={ref}
      style={{
        textAlign: "center",
        minHeight: "6rem",
        padding: "2rem",
        // backgroundColor: "rgba(0, 0, 0, 0.05)",

        borderRadius: "8px",
        margin: "1rem",
      }}
    >
      {!mounted ? (
        <div>Initializing...</div>
      ) : error ? (
        <div style={{ color: "red" }}>Error: {error}</div>
      ) : loading ? (
        <div>
          <div
            style={{
              fontSize: "4rem",
              fontWeight: "bold",
              color: "var(--text-color)",
            }}
          >
            0
          </div>
          <div style={{ fontSize: "1.5rem", color: "var(--text-color)" }}>
            Images
          </div>
        </div>
      ) : count !== null ? (
        <div>
          <div
            style={{
              fontSize: "4rem",
              fontWeight: "bold",
              color: "var(--text-color)",
            }}
          >
            {animatedCount.toLocaleString()}
          </div>
          <div style={{ fontSize: "1.5rem", color: "var(--text-color)" }}>
            Images
          </div>
        </div>
      ) : (
        <div style={{ color: "#999" }}>Waiting to load...</div>
      )}
    </div>
  );
};

const ReceiptCounts = () => {
  const [mounted, setMounted] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [animatedCount, setAnimatedCount] = useState<number>(0);
  const { ref, inView } = useInView({
    triggerOnce: true,
    threshold: 0,
  });

  useEffect(() => {
    console.log("ReceiptCounts component mounted");
    setMounted(true);
  }, []);

  useEffect(() => {
    console.log("ReceiptCounts state:", {
      mounted,
      inView,
      loading,
      count,
      error,
    });
    if (mounted && inView && !loading && count === null && !error) {
      console.log("ReceiptCounts - Starting fetch...");
      setLoading(true);
      api
        .fetchReceiptCount()
        .then((fetchedCount: number) => {
          console.log("ReceiptCounts - Success:", fetchedCount);
          setCount(fetchedCount);
          setError(null);
          // Start simple counter animation
          let current = 0;
          const target = fetchedCount;
          const increment = Math.ceil(target / 50); // 50 steps
          const timer = setInterval(() => {
            current += increment;
            if (current >= target) {
              setAnimatedCount(target);
              clearInterval(timer);
            } else {
              setAnimatedCount(current);
            }
          }, 20);
        })
        .catch((error: Error) => {
          console.error("ReceiptCounts - Error:", error);
          setError(error.message || "Failed to load receipt count");
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [mounted, inView, loading, count, error]);

  // Always render the container with ref attached
  return (
    <div
      ref={ref}
      style={{
        textAlign: "center",
        minHeight: "6rem",
        padding: "2rem",
        borderRadius: "8px",
        margin: "1rem",
      }}
    >
      {!mounted ? (
        <div>Initializing...</div>
      ) : error ? (
        <div style={{ color: "red" }}>Error: {error}</div>
      ) : loading ? (
        <div>
          <div
            style={{
              fontSize: "4rem",
              fontWeight: "bold",
              color: "var(--text-color)",
            }}
          >
            0
          </div>
          <div style={{ fontSize: "1.5rem", color: "var(--text-color)" }}>
            Receipts
          </div>
        </div>
      ) : count !== null ? (
        <div>
          <div
            style={{
              fontSize: "4rem",
              fontWeight: "bold",
              color: "var(--text-color)",
            }}
          >
            {animatedCount.toLocaleString()}
          </div>
          <div style={{ fontSize: "1.5rem", color: "var(--text-color)" }}>
            Receipts
          </div>
        </div>
      ) : (
        <div>Waiting to load...</div>
      )}
    </div>
  );
};

// Export client-side only components with no loading component
export const ClientImageCounts = dynamic(() => Promise.resolve(ImageCounts), {
  ssr: false,
});

export const ClientReceiptCounts = dynamic(
  () => Promise.resolve(ReceiptCounts),
  {
    ssr: false,
  }
);

// Debug versions without dynamic import
export const SimpleImageCounts = ImageCounts;
export const SimpleReceiptCounts = ReceiptCounts;
