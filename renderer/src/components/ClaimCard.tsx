import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { ACCENT, PANEL, PANEL_BORDER, SHADOW, SPRING_SOFT } from "../theme";

// A sentence quoted from the source, word by word, with where it appears.
// Props: quote (validated server-side as an exact substring of the source), source_ref?.
export const ClaimCard: React.FC<{ props: Record<string, any> }> = ({ props }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: f, fps, config: SPRING_SOFT });
  const words = String(props.quote ?? "").split(" ");
  return (
    <div
      style={{
        maxWidth: 1250, padding: "44px 56px", borderRadius: 28, background: PANEL,
        border: `1px solid ${PANEL_BORDER}`, boxShadow: SHADOW, position: "relative",
        opacity: enter, transform: `translateY(${(1 - enter) * 40}px)`,
        display: "flex", flexDirection: "column", gap: 24,
      }}
    >
      <span style={{ position: "absolute", left: 26, top: -18, fontSize: 110, color: ACCENT, fontWeight: 800 }}>
        “
      </span>
      <div style={{ fontSize: 44, fontWeight: 700, lineHeight: 1.35, letterSpacing: -0.5 }}>
        {words.map((w, i) => {
          const on = f >= 8 + i * 1.6;
          return (
            <span
              key={i}
              style={{
                marginRight: 13, display: "inline-block",
                color: on ? "#fff" : "rgba(255,255,255,.25)",
                transition: "none",
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
      {props.source_ref ? (
        <div
          style={{
            fontSize: 26, fontWeight: 600, color: ACCENT,
            opacity: interpolate(f, [20, 32], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
          }}
        >
          — {props.source_ref}
        </div>
      ) : null}
    </div>
  );
};
