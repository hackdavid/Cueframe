import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { ACCENT, MONO, PANEL, PANEL_BORDER, SHADOW, SPRING_SOFT } from "../theme";

// Weekly commit activity over the last year, bars growing left to right.
// Resolved props: values (52 numbers), value (total, display string), fact_label.
export const ContribGraph: React.FC<{ props: Record<string, any> }> = ({ props }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: f, fps, config: SPRING_SOFT });
  const values: number[] = Array.isArray(props.values) ? props.values : [];
  const max = Math.max(1, ...values);
  const W = 1300;
  const H = 380;
  const n = values.length || 1;
  const bw = W / n;
  return (
    <div
      style={{
        padding: "36px 44px", borderRadius: 26, background: PANEL,
        border: `1px solid ${PANEL_BORDER}`, boxShadow: SHADOW,
        opacity: enter, transform: `translateY(${(1 - enter) * 40}px)`,
        display: "flex", flexDirection: "column", gap: 18,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-end", gap: Math.max(2, bw * 0.18), width: W, height: H }}>
        {values.map((v, i) => {
          const p = interpolate(f, [8 + i * 0.6, 8 + i * 0.6 + 12], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          return (
            <div
              key={i}
              style={{
                width: bw * 0.72, height: Math.max(3, (v / max) * H * p),
                background: i === n - 1 ? ACCENT : "rgba(124,92,255,.55)",
                borderRadius: 4,
              }}
            />
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 28, fontWeight: 600, opacity: 0.75 }}>{props.fact_label}</span>
        <span style={{ fontFamily: MONO, fontSize: 34, color: ACCENT }}>
          {props.value} <span style={{ fontSize: 24, opacity: 0.7 }}>commits</span>
        </span>
      </div>
    </div>
  );
};
