import Image from "next/image";
import React from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

/**
 * The Grok Bot app icon, bouncing the way a Dock icon does when you click
 * it and the app is still launching. Bounces three times on entering the
 * viewport, then rests. Honors prefers-reduced-motion.
 */
const SIZE = 128;

const STYLE = `
@keyframes grok-dock-bounce {
  0%   { transform: translateY(0); animation-timing-function: cubic-bezier(.2,.6,.3,1); }
  32%  { transform: translateY(-44px); animation-timing-function: cubic-bezier(.6,0,.8,.4); }
  64%  { transform: translateY(0); animation-timing-function: cubic-bezier(.2,.6,.3,1); }
  82%  { transform: translateY(-14px); animation-timing-function: cubic-bezier(.6,0,.8,.4); }
  100% { transform: translateY(0); }
}
@keyframes grok-dock-shadow {
  0%   { transform: scaleX(1); opacity: .35; }
  32%  { transform: scaleX(.55); opacity: .12; }
  64%  { transform: scaleX(1); opacity: .35; }
  82%  { transform: scaleX(.8); opacity: .22; }
  100% { transform: scaleX(1); opacity: .35; }
}
.grok-dock { position: relative; width: ${SIZE}px; height: ${SIZE + 36}px; margin: 0 auto; }
.grok-dock img { position: absolute; left: 0; top: 0; will-change: transform; }
.grok-dock i { position: absolute; left: 14px; right: 14px; bottom: 6px; height: 14px; border-radius: 50%;
  background: radial-gradient(ellipse at center, rgba(var(--text-color-rgb), .45), rgba(var(--text-color-rgb), 0) 70%);
  opacity: .35; }
.grok-dock[data-bounce="true"] img { animation: grok-dock-bounce .9s 3 both; }
.grok-dock[data-bounce="true"] i { animation: grok-dock-shadow .9s 3 both; }
@media (prefers-reduced-motion: reduce) {
  .grok-dock[data-bounce="true"] img, .grok-dock[data-bounce="true"] i { animation: none; }
}
`;

const GrokBotLogo: React.FC = () => {
  const [ref, inView] = useOptimizedInView({ threshold: 0.6 });
  return (
    <div
      ref={ref}
      style={{ display: "flex", justifyContent: "center", margin: "1rem 0" }}
    >
      <style>{STYLE}</style>
      <div className="grok-dock" data-bounce={inView ? "true" : "false"}>
        <Image
          src="/grok-bot-icon.svg"
          alt="Grok Bot app icon"
          width={SIZE}
          height={SIZE}
          priority={false}
        />
        <i aria-hidden="true" />
      </div>
    </div>
  );
};

export default GrokBotLogo;
