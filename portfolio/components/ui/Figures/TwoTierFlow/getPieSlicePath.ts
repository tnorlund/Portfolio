/** Generate SVG path for a pie slice from 12 o'clock, filling clockwise */
export const getPieSlicePath = (
  progress: number,
  cx: number,
  cy: number,
  r: number,
): string => {
  if (progress <= 0) return "";
  if (progress >= 100)
    return `M ${cx} ${cy} m -${r} 0 a ${r} ${r} 0 1 0 ${r * 2} 0 a ${r} ${r} 0 1 0 -${r * 2} 0`;

  const angle = (progress / 100) * 2 * Math.PI;
  const startAngle = -Math.PI / 2;
  const endAngle = startAngle + angle;

  const x1 = cx + r * Math.cos(startAngle);
  const y1 = cy + r * Math.sin(startAngle);
  const x2 = cx + r * Math.cos(endAngle);
  const y2 = cy + r * Math.sin(endAngle);

  const largeArcFlag = progress > 50 ? 1 : 0;

  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArcFlag} 1 ${x2} ${y2} Z`;
};
