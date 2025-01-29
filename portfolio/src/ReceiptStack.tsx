import React, { useEffect, useState } from "react";
import { fetchReceipts } from "./api";
import { Receipt, ReceiptApiResponse } from "./interfaces";

// Intersection Observer
import { useInView } from "react-intersection-observer";

// React Spring
import { useSprings, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

const ReceiptStack: React.FC = () => {
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [rotations, setRotations] = useState<number[]>([]);

  const [ref, inView] = useInView({
    threshold: 0.1, // 10% visible
    triggerOnce: true, // animate only the first time it appears
  });

  useEffect(() => {
    const getReceipts = async () => {
      try {
        const response: ReceiptApiResponse = await fetchReceipts(15);
        // No need for `Object.values(response)` or `.map(entry => entry.receipt)` anymore
        setReceipts(response);

        // Generate random rotations for each receipt
        const newRotations = response.map(() => Math.random() * 50 - 25);
        setRotations(newRotations);
      } catch (error) {
        console.error("Error fetching receipts:", error);
      }
    };

    getReceipts();
  }, []);

  const [springs, api] = useSprings(receipts.length, (index) => ({
    from: {
      opacity: 0,
      transform: "translate(0px, -50px) rotate(0deg)",
    },
    to: {
      opacity: 0,
      transform: "translate(0px, -50px) rotate(0deg)",
    },
  }));

  // 3) When in view, animate each receipt in a staggered fashion
  useEffect(() => {
    if (inView && receipts.length > 0) {
      api.start((index) => {
        const rotation = rotations[index] || 0;
        const topOffset = index * 10;
        const leftOffset = index * 10;

        return {
          opacity: 1,
          transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
          delay: index * 150, // 150ms stagger between receipts
        };
      });
    }
  }, [inView, api, rotations, receipts.length]);

  return (
    <div
      ref={ref} // Attach Intersection Observer to this container
      style={{
        width: "100%",
        display: "flex",
        justifyContent: "center",
        marginTop: "2rem",
      }}
    >
      {/* Fixed-size, relative container so absolutely positioned items stack */}
      <div
        style={{
          position: "relative",
          width: "150px", // Stacking area width
          minHeight: "475px",
        }}
      >
        {springs.map((styleProps, index) => {
          const receipt = receipts[index];
          if (!receipt) return null;

          const cdnUrl = isDevelopment
            ? `https://dev.tylernorlund.com/${receipt.cdn_s3_key}`
            : `https://www.tylernorlund.com/${receipt.cdn_s3_key}`;

          return (
            <animated.div
              key={receipt.id || index}
              style={{
                // Absolutely positioned so "topOffset" & "leftOffset" come from transform
                position: "absolute",
                width: "100px",
                border: "1px solid #ccc",
                backgroundColor: "#fff",
                boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
                // Merge the per-item animated styles
                ...styleProps,
              }}
            >
              <img
                src={cdnUrl}
                alt={`Receipt ${receipt.id}`}
                style={{
                  width: "100%",
                  height: "auto",
                  display: "block",
                }}
              />
            </animated.div>
          );
        })}
      </div>
    </div>
  );
};

export default ReceiptStack;
