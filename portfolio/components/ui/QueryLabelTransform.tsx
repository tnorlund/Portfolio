import { animated, useSpring } from "@react-spring/web";
import { useEffect, useState } from "react";
import { useInView } from "react-intersection-observer";

interface QueryLabelTransformProps {
    /** The original query to display */
    query: string;
    /** The transformed query with labels */
    transformed: string;
    /** Delay before starting animation (ms) */
    delay?: number;
}

/**
 * Displays a query string at the top, then animates the transformed
 * version with labels appearing below when scrolled into view.
 */
const QueryLabelTransform = ({
    query,
    transformed,
    delay = 800,
}: QueryLabelTransformProps) => {
    const [ref, inView] = useInView({
        threshold: 0.3,
        triggerOnce: false,
        rootMargin: "50px",
    });
    const [hasBeenInView, setHasBeenInView] = useState(false);
    const [showTransformed, setShowTransformed] = useState(false);
    const [displayText, setDisplayText] = useState("");
    const [isScrambling, setIsScrambling] = useState(false);

    // Track when element has been in view (for animation trigger)
    useEffect(() => {
        if (inView && !hasBeenInView) {
            setHasBeenInView(true);
        }
    }, [inView, hasBeenInView]);

    // Reset when out of view
    useEffect(() => {
        if (!inView) {
            setShowTransformed(false);
            setDisplayText("");
            setIsScrambling(false);
            setHasBeenInView(false);
        }
    }, [inView]);

    // Trigger animation when in view
    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | null = null;
        let scrambleInterval: ReturnType<typeof setInterval> | null = null;

        if (hasBeenInView && !showTransformed) {
            timer = setTimeout(() => {
                setShowTransformed(true);
                setIsScrambling(true);

                // Scramble effect
                let scrambleCount = 0;
                scrambleInterval = setInterval(() => {
                    const chars = "█▓▒░";
                    setDisplayText(
                        Array.from({ length: transformed.length })
                            .map(() => chars[Math.floor(Math.random() * chars.length)])
                            .join("")
                    );
                    scrambleCount++;
                    if (scrambleCount >= 6) {
                        if (scrambleInterval) {
                            clearInterval(scrambleInterval);
                            scrambleInterval = null;
                        }
                        setIsScrambling(false);
                        setDisplayText(transformed);
                    }
                }, 50);
            }, delay);
        }

        return () => {
            if (timer) {
                clearTimeout(timer);
            }
            if (scrambleInterval) {
                clearInterval(scrambleInterval);
            }
        };
    }, [hasBeenInView, showTransformed, delay, transformed]);

    // Transformed line animation
    const transformedSpring = useSpring({
        opacity: showTransformed ? 1 : 0,
        transform: showTransformed ? "translateY(0px)" : "translateY(-10px)",
        config: { tension: 150, friction: 16 },
    });

    return (
        <div
            ref={ref}
            style={{
                background: "var(--code-background)",
                borderRadius: "4px",
                padding: "1em",
                fontSize: "0.9rem",
                margin: "2em 0",
                fontFamily: "Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
                color: "var(--color-text)",
                lineHeight: 1.6,
                position: "relative",
                overflow: "hidden",
            }}
        >
            {/* Original query line */}
            <div style={{ marginBottom: showTransformed ? "0.75em" : 0 }}>
                <span
                    style={{
                        color: "var(--color-text)",
                        marginRight: "0.75em",
                        fontWeight: 600,
                        opacity: 0.5,
                    }}
                >
                    &gt;
                </span>
                <span>{query}</span>
                {!showTransformed && (
                    <span
                        style={{
                            display: "inline-block",
                            width: "2px",
                            height: "1.2em",
                            background: "var(--color-text)",
                            marginLeft: "4px",
                            verticalAlign: "text-bottom",
                            animation: "blink 1s step-end infinite",
                        }}
                    />
                )}
            </div>

            {/* Transformed query line */}
            {showTransformed && (
                <animated.div style={transformedSpring}>
                    <span
                        style={{
                            color: "var(--color-text)",
                            marginRight: "0.75em",
                            fontWeight: 600,
                            opacity: 0.5,
                        }}
                    >
                        →
                    </span>
                    <TransformedText text={displayText} isScrambling={isScrambling} />
                    <span
                        style={{
                            display: "inline-block",
                            width: "2px",
                            height: "1.2em",
                            background: "var(--color-text)",
                            marginLeft: "4px",
                            verticalAlign: "text-bottom",
                            animation: "blink 1s step-end infinite",
                        }}
                    />
                </animated.div>
            )}

            <style jsx>{`
        @keyframes blink {
          0%, 50% { opacity: 1; }
          51%, 100% { opacity: 0; }
        }
      `}</style>
        </div>
    );
};

interface TransformedTextProps {
    text: string;
    isScrambling: boolean;
}

/**
 * Renders the transformed text with labels highlighted
 */
const TransformedText = ({ text, isScrambling }: TransformedTextProps) => {
    if (isScrambling) {
        return <span>{text}</span>;
    }

    // Parse labels in the text (words that are ALL_CAPS with underscores)
    const labelRegex = /\b([A-Z][A-Z_]+)\b/g;
    const parts: Array<{ type: "text" | "label"; content: string }> = [];
    let lastIndex = 0;
    let match;

    while ((match = labelRegex.exec(text)) !== null) {
        // Add text before the match
        if (match.index > lastIndex) {
            parts.push({ type: "text", content: text.slice(lastIndex, match.index) });
        }
        // Add the label
        parts.push({ type: "label", content: match[1] });
        lastIndex = match.index + match[0].length;
    }

    // Add remaining text
    if (lastIndex < text.length) {
        parts.push({ type: "text", content: text.slice(lastIndex) });
    }

    return (
        <>
            {parts.map((part, i) => {
                if (part.type === "label") {
                    return (
                        <span
                            key={i}
                            style={{
                                display: "inline-block",
                                padding: "2px 6px",
                                borderRadius: "4px",
                                fontWeight: 600,
                                background: "var(--color-text)",
                                color: "var(--color-background)",
                                marginLeft: "2px",
                                marginRight: "2px",
                            }}
                        >
                            {part.content}
                        </span>
                    );
                }
                return <span key={i}>{part.content}</span>;
            })}
        </>
    );
};

export default QueryLabelTransform;
