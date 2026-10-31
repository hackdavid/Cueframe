import React from "react";
import { Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { ACCENT_SOFT, MONO, SPRING_SOFT } from "../theme";

// The star count ticking up from zero to the real number. Resolved props: number,
// value (display string of the final count), fact_label, sub?.
export const StarCounter: React.FC<{ props: Record<string, any> }> = ({ props }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: f, fps, config: SPRING_SOFT });
  const target = Number(props.number ?? 0);
  const p = interpolate(f, [6, 6 + 2.2 * fps], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const shown = Math.round(target * p);
  const done = p >= 1;
  const label = props.fact_label || "GitHub stars";
  const sub = props.sub && props.sub !== label ? props.sub : null; // don't echo the label
  return (
    <div
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 18,
        opacity: enter, transform: `translateY(${(1 - enter) * 40}px)`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
        <span
          style={{
            fontSize: 110, lineHeight: 1, color: "#f0c050",
            transform: `scale(${done ? 1 + 0.06 * Math.sin(f / 4) : 1})`,
            textShadow: "0 0 40px rgba(240,192,80,.45)",
          }}
        >
          ★
        </span>
        <span style={{ fontFamily: MONO, fontSize: 130, fontWeight: 500, letterSpacing: -4 }}>
          {shown.toLocaleString("en-US")}
        </span>
      </div>
      <div style={{ fontSize: 34, fontWeight: 600, opacity: 0.8 }}>
        {label}
        {sub ? <span style={{ color: ACCENT_SOFT }}> · {sub}</span> : null}
      </div>
    </div>
  );
};
