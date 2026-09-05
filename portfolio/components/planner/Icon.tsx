import type { CSSProperties } from "react";
const paths: Record<string, React.ReactNode> = {
  book: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="1.5" />
      <path d="M8 3v18M3 7h4M3 11h4M3 15h4M3 19h4" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  left: <path d="m15 5-7 7 7 7" />,
  right: <path d="m9 5 7 7-7 7" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  areas: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </>
  ),
  edit: (
    <>
      <path d="m4 16 12-12 4 4L8 20l-5 1Z" />
      <path d="m14 6 4 4" />
    </>
  ),
};
export function Icon({
  name,
  size = 18,
  style,
}: {
  name: string;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      {paths[name]}
    </svg>
  );
}
