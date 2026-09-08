import Image from "next/image";
import React from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

/**
 * An app icon doing the single Dock bounce macOS plays when you open an
 * app, once, when it scrolls into view. Honors prefers-reduced-motion.
 */
const SIZE = 128;

const STYLE = `
@keyframes dock-bounce {
  0%   { transform: translateY(0); animation-timing-function: cubic-bezier(.2,.6,.3,1); }
  45%  { transform: translateY(-40px); animation-timing-function: cubic-bezier(.6,0,.8,.4); }
  100% { transform: translateY(0); }
}
.dock-icon { position: relative; width: ${SIZE}px; height: ${SIZE + 24}px; margin: 0 auto; }
.dock-icon img { position: absolute; left: 0; top: 24px; will-change: transform; }
.dock-icon[data-bounce="true"] img { animation: dock-bounce .7s 1 both; }
@media (prefers-reduced-motion: reduce) {
  .dock-icon[data-bounce="true"] img { animation: none; }
}
`;

const DockIcon: React.FC<{ src: string; alt: string }> = ({ src, alt }) => {
  const [ref, inView] = useOptimizedInView({ threshold: 0.6 });
  return (
    <div
      ref={ref}
      style={{ display: "flex", justifyContent: "center", margin: "1rem 0" }}
    >
      <style>{STYLE}</style>
      <div className="dock-icon" data-bounce={inView ? "true" : "false"}>
        <Image
          src={src}
          alt={alt}
          width={SIZE}
          height={SIZE}
          priority={false}
        />
      </div>
    </div>
  );
};

export default DockIcon;
