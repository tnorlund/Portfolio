import React, { useEffect, useState } from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
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
  const { ref, inView } = useOptimizedInView({
    triggerOnce: true,
    threshold: 0,
  });

  // Prefetch the count as soon as the component mounts
  useEffect(() => {
    setMounted(true);
    setLoading(true);
    api
      .fetchImageCount()
      .then((fetchedCount: ImageCountApiResponse) => {
        setCount(fetchedCount.count);
        setError(null);
      })
      .catch((err: Error) => {
        console.error("ImageCounts - Error:", err);
        setError(err.message || "Failed to load image count");
      })
      .finally(() => setLoading(false));
  }, []);

  // Start the animation once the element scrolls into view
  useEffect(() => {
    if (!inView || count === null) return;

    let current = 0;
    const target = count;
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

    return () => clearInterval(timer);
  }, [inView, count]);

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
  const { ref, inView } = useOptimizedInView({
    triggerOnce: true,
    threshold: 0,
  });

  // Prefetch the count on mount
  useEffect(() => {
    setMounted(true);
    setLoading(true);
    api
      .fetchReceiptCount()
      .then((fetchedCount: number) => {
        setCount(fetchedCount);
        setError(null);
      })
      .catch((err: Error) => {
        console.error("ReceiptCounts - Error:", err);
        setError(err.message || "Failed to load receipt count");
      })
      .finally(() => setLoading(false));
  }, []);

  // Start the animation when visible
  useEffect(() => {
    if (!inView || count === null) return;

    let current = 0;
    const target = count;
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

    return () => clearInterval(timer);
  }, [inView, count]);

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
