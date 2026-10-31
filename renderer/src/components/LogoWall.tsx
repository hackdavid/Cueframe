import React from "react";
import { spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { PANEL, PANEL_BORDER, SHADOW, SPRING_POP } from "../theme";

// Avatars of the GitHub orgs that use the project, popping in as a grid. Logos are
// downloaded server-side at build time and served from the bundle's public dir.
// Props: orgs (logins). Needs the tutorial id to build the staticFile path.
export const LogoWall: React.FC<{ props: Record<string, any>; tid: string }> = ({ props, tid }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const orgs: string[] = Array.isArray(props.orgs) ? props.orgs : [];
  const cols = Math.min(4, Math.ceil(Math.sqrt(orgs.length)));
  const size = orgs.length > 6 ? 120 : 150;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
      <div style={{ fontSize: 34, fontWeight: 700, opacity: 0.85 }}>Used by teams at</div>
      <div
        style={{
          display: "grid", gridTemplateColumns: `repeat(${cols}, ${size}px)`,
          gap: 26, padding: "30px 36px", borderRadius: 28,
          background: PANEL, border: `1px solid ${PANEL_BORDER}`, boxShadow: SHADOW,
        }}
      >
        {orgs.map((org, i) => {
          const p = spring({ frame: f - 6 - i * 4, fps, config: SPRING_POP });
          return (
            <img
              key={org}
              src={staticFile(`${tid}/logos/${String(org).toLowerCase()}.png`)}
              title={org}
              style={{
                width: size, height: size, borderRadius: size / 4,
                transform: `scale(${p})`, opacity: p,
                border: `1px solid ${PANEL_BORDER}`, background: "#fff",
              }}
            />
          );
        })}
      </div>
    </div>
  );
};
