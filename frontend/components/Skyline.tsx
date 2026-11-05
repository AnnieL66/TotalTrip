/** Decorative Chicago skyline, after the deck's illustration. */
export function Skyline({ className = "" }: { className?: string }) {
  const bars = [
    [20, 70, 0], [55, 52, 1], [90, 95, 0], [125, 120, 1], [160, 82, 0], [200, 60, 1], [235, 110, 0],
    [270, 75, 1], [310, 58, 0], [345, 100, 1], [380, 68, 0], [415, 88, 1], [450, 54, 0], [485, 92, 1],
  ];
  return (
    <svg viewBox="0 0 540 170" className={className} role="img" aria-label="Illustrated Chicago skyline">
      <rect width="540" height="170" fill="#efe1cf" />
      <circle cx="400" cy="70" r="38" fill="#e7a35a" opacity="0.85" />
      {bars.map(([x, h, alt], i) => (
        <rect key={i} x={x} y={150 - h} width="30" height={h} fill={alt ? "#8a6aa8" : "#4a1d6e"} />
      ))}
      <rect x="133" y="22" width="4" height="10" fill="#4a1d6e" />
      <rect x="0" y="150" width="540" height="20" fill="#cfd9e3" />
      <rect x="40" y="158" width="60" height="2" fill="#fff" opacity="0.7" />
      <rect x="250" y="161" width="80" height="2" fill="#fff" opacity="0.7" />
      <rect x="420" y="157" width="50" height="2" fill="#fff" opacity="0.7" />
    </svg>
  );
}
