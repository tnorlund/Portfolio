import React, { useEffect, useState } from "react";
import { useSpring, animated } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import { fetchImageCount, fetchReceiptCount } from "./api";

const ImageCounts = () => {
  const [count, setCount] = useState(0);
  const { ref, inView } = useInView({
    triggerOnce: true,
    threshold: 0.5,
  });

  // Create a spring animation that will animate from 1 to the fetched count.
  const springProps = useSpring({
    from: { number: 1 },
    to: { number: count },
    config: { duration: 1000 },
  });

  useEffect(() => {
    if (inView) {
      fetchImageCount()
        .then((fetchedCount) => {
          setCount(fetchedCount);
        })
        .catch((error) => {
          console.error("Error fetching image count:", error);
        });
    }
  }, [inView]);

  return (
    <div ref={ref} style={{ textAlign: "center" }}>
      {/* Only render the text & animation if count > 0 */}
      {count > 0 && (
        <>
          <animated.span style={{ fontSize: "4rem", fontWeight: "bold" }}>
            {springProps.number.to((n) => Math.floor(n).toLocaleString())}
          </animated.span>{" "}
          Images
        </>
      )}
    </div>
  );
};

const ReceiptCounts = () => {
  const [count, setCount] = useState(0);
  const { ref, inView } = useInView({
    triggerOnce: true,
    threshold: 0.5,
  });

  // Create a spring animation that will animate from 1 to the fetched count.
  const springProps = useSpring({
    from: { number: 1 },
    to: { number: count },
    config: { duration: 1000 },
  });

  useEffect(() => {
    if (inView) {
      fetchReceiptCount()
        .then((fetchedCount) => {
          setCount(fetchedCount);
        })
        .catch((error) => {
          console.error("Error fetching receipt count:", error);
        });
    }
  }, [inView]);

  return (
    <div ref={ref} style={{ textAlign: "center" }}>
      {/* Only render the text & animation if count > 0 */}
      {count > 0 && (
        <>
          <animated.span style={{ fontSize: "4rem", fontWeight: "bold" }}>
            {springProps.number.to((n) => Math.floor(n).toLocaleString())}
          </animated.span>{" "}
          Receipts
        </>
      )}
    </div>
  );
};

export { ReceiptCounts, ImageCounts };