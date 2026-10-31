import React from "react";
import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { ACCENT, MONO, PANEL, PANEL_BORDER, SHADOW, SPRING_POP } from "../theme";

// A big verified number that pops in with a caption. Props are resolved server-side:
// value (display string), fact_label, label?, prefix?, suffix?.
export const StatChip: React.FC<{ props: Record<string, any> }> = ({ props }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f, fps, config: SPRING_POP });
  const label = props.label || props.fact_label || "";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      <div
        style={{
          padding: "26px 48px", borderRadius: 26, background: PANEL,
          border: `1px solid ${PANEL_BORDER}`, boxShadow: SHADOW,
          transform: `scale(${p})`, opacity: p,
          display: "flex", flexDirection: "column", alignItems: "center", gap: 8,
        }}
      >
        <div style={{ fontFamily: MONO, fontSize: 96, fontWeight: 500, color: "#fff", letterSpacing: -2 }}>
          {props.prefix ?? ""}
          <span style={{ color: ACCENT }}>{props.value}</span>
          {props.suffix ?? ""}
        </div>
        {label && <div style={{ fontSize: 30, fontWeight: 600, opacity: 0.75 }}>{label}</div>}
      </div>
    </div>
  );
};
