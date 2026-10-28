import React from "react";
import {
  AbsoluteFill, Audio, Easing, Freeze, interpolate, OffthreadVideo, Sequence, spring,
  staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";
import type { Camera, Highlight, State, Tutorial } from "./types";

const { fontFamily: SANS } = loadInter("normal", { weights: ["400", "600", "800"], subsets: ["latin"] });
const { fontFamily: MONO } = loadMono("normal", { weights: ["500"], subsets: ["latin"] });

const SRC_W = 1920;
const SRC_H = 1080;
// The recording is shown in a card; the card never changes size (no rescaling of the frame).
const CARD = { x: 150, y: 96, w: 1620, h: 911 };
const K = CARD.w / SRC_W;
const ACCENT = "#7c5cff";
const GREEN = "#3fb950";

export const framesOf = (s: State, fps: number) => Math.round(s.duration * fps);

const src = (u: string | null) => (u && /^https?:/.test(u) ? u : staticFile(u ?? ""));

export const TutorialVideo: React.FC<{ tutorial: Tutorial }> = ({ tutorial: t }) => {
  let from = 0;
  return (
    <AbsoluteFill style={{ fontFamily: SANS, color: "#fff" }}>
      <Backdrop />
      {t.states.map((s, i) => {
        const dur = framesOf(s, t.fps);
        const seq = (
          <Sequence key={s.id} from={from} durationInFrames={dur} name={s.id}>
            <StateView t={t} s={s} index={i} />
          </Sequence>
        );
        from += dur;
        return seq;
      })}
    </AbsoluteFill>
  );
};

const Backdrop: React.FC = () => {
  const f = useCurrentFrame();
  const a = (f / 6) % 360;
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(1200px 700px at ${30 + 10 * Math.sin(f / 90)}% 20%, #2a1f6e 0%, transparent 60%),
          radial-gradient(900px 600px at ${80 - 8 * Math.cos(f / 120)}% 90%, #0e4d64 0%, transparent 60%),
          linear-gradient(${a * 0 + 160}deg, #0a0b14, #0d1020)`,
      }}
    >
      <AbsoluteFill
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
          backgroundPosition: `0 ${(f * 0.4) % 64}px`,
        }}
      />
    </AbsoluteFill>
  );
};

const StateView: React.FC<{ t: Tutorial; s: State; index: number }> = ({ t, s, index }) => {
  const steps = t.steps;
  return (
    <AbsoluteFill>
      {s.kind === "title" && <TitleCard t={t} />}
      {s.kind === "outro" && <Outro t={t} />}
      {s.source && <Screen t={t} s={s} />}
      {s.source && <TopBar s={s} steps={steps} />}
      <Captions s={s} />
      {s.narration.map((n, i) =>
        n.audio_url ? (
          <Sequence key={i} from={Math.round(n.at * t.fps)} layout="none">
            <Audio src={src(n.audio_url)} />
          </Sequence>
        ) : null,
      )}
      {s.sfx.map((x, i) => (
        <Sequence key={`sfx${i}`} from={Math.round(x.t * t.fps)} durationInFrames={x.duration ? Math.round(x.duration * t.fps) : undefined} layout="none">
          <Audio src={staticFile(`${t.id}/audio/sfx_${x.kind}.mp3`)} volume={SFX_VOL[x.kind]} />
        </Sequence>
      ))}
      {index > 0 && <Flash />}
    </AbsoluteFill>
  );
};

const SFX_VOL = { click: 0.5, keys: 0.22, whoosh: 0.35, success: 0.4, pop: 0.4 } as const;

// A quick light sweep at each state boundary hides the cut.
const Flash: React.FC = () => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 8], [0.35, 0], { extrapolateRight: "clamp" });
  return <AbsoluteFill style={{ background: "#fff", opacity: o, pointerEvents: "none" }} />;
};

const camAt = (cam: Camera[], tt: number): Camera => {
  if (!cam.length) return { t: 0, zoom: 1, cx: SRC_W / 2, cy: SRC_H / 2 };
  if (tt <= cam[0].t) return cam[0];
  for (let i = 1; i < cam.length; i++) {
    const a = cam[i - 1];
    const b = cam[i];
    if (tt <= b.t) {
      const p = Easing.inOut(Easing.cubic)((tt - a.t) / Math.max(0.001, b.t - a.t));
      return { t: tt, zoom: a.zoom + (b.zoom - a.zoom) * p, cx: a.cx + (b.cx - a.cx) * p, cy: a.cy + (b.cy - a.cy) * p };
    }
  }
  return cam[cam.length - 1];
};

const clampCam = (c: Camera) => {
  const vw = SRC_W / c.zoom;
  const vh = SRC_H / c.zoom;
  const cx = Math.min(Math.max(c.cx, vw / 2), SRC_W - vw / 2);
  const cy = Math.min(Math.max(c.cy, vh / 2), SRC_H - vh / 2);
  return { ...c, cx, cy };
};

const Screen: React.FC<{ t: Tutorial; s: State }> = ({ t, s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const tt = f / fps;
  const enter = spring({ frame: f, fps, config: { damping: 18, stiffness: 120 } });
  const switched = s.sfx.some((x) => x.kind === "whoosh" && x.t < 0.1);
  const slide = switched ? interpolate(enter, [0, 1], [140, 0]) : interpolate(enter, [0, 1], [24, 0]);
  const c = clampCam(camAt(s.camera, tt));
  // transform maps source point (cx,cy) to the card centre at the given zoom
  const tx = CARD.w / 2 - c.cx * K * c.zoom;
  const ty = CARD.h / 2 - c.cy * K * c.zoom;
  const src0 = s.source!;
  return (
    <div
      style={{
        position: "absolute", left: CARD.x, top: CARD.y, width: CARD.w, height: CARD.h,
        borderRadius: 22, overflow: "hidden", transform: `translateY(${slide}px) scale(${0.96 + 0.04 * enter})`,
        opacity: enter, boxShadow: "0 40px 120px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.08)",
        background: "#000",
      }}
    >
      <div style={{ position: "absolute", left: 0, top: 0, width: SRC_W * K, height: SRC_H * K, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${c.zoom})` }}>
        {(() => {
          const video = (
            <OffthreadVideo
              src={src(t.recording_url)}
              startFrom={Math.round(src0.t_in * fps)}
              playbackRate={src0.rate}
              muted
              style={{ width: SRC_W * K, height: SRC_H * K }}
            />
          );
          // Narration edited to run longer than the captured step: hold its last frame.
          const last = Math.floor(((src0.t_out - src0.t_in) / src0.rate) * fps) - 1;
          return f > last ? <Freeze frame={last}>{video}</Freeze> : video;
        })()}
        {s.highlights.map((h, i) => <HighlightBox key={i} h={h} tt={tt} zoom={c.zoom} />)}
      </div>
      {s.badge && <Badge text={s.badge} s={s} />}
    </div>
  );
};

// Labels sit beside the box (never over the text below it); wide boxes take the label inside, at their right end.
const labelAt = (h: Highlight, zoom: number): React.CSSProperties =>
  h.x + h.w < SRC_W * 0.6
    ? { left: "100%", top: "50%", marginLeft: 14 / zoom, transform: "translateY(-50%)" }
    : { right: 10 / zoom, top: "50%", transform: "translateY(-50%)" };

const HighlightBox: React.FC<{ h: Highlight; tt: number; zoom: number }> = ({ h, tt, zoom }) => {
  if (tt < h.t_in || tt > h.t_out + 0.3) return null;
  const p = interpolate(tt, [h.t_in, h.t_in + 0.35, h.t_out, h.t_out + 0.3], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const pulse = 0.5 + 0.5 * Math.sin((tt - h.t_in) * 5);
  const pad = 8 / zoom;
  return (
    <div style={{ position: "absolute", left: h.x * K - pad, top: h.y * K - pad, width: h.w * K + 2 * pad, height: h.h * K + 2 * pad, opacity: p,
      border: `${3 / zoom}px solid ${ACCENT}`, borderRadius: 10 / zoom,
      boxShadow: `0 0 ${(18 + 14 * pulse) / zoom}px ${ACCENT}, inset 0 0 ${14 / zoom}px rgba(124,92,255,.35)`,
      transform: `scale(${0.92 + 0.08 * p})` }}>
      {h.label && (
        <div style={{ position: "absolute", ...labelAt(h, zoom), padding: `${5 / zoom}px ${12 / zoom}px`, background: ACCENT,
          borderRadius: 8 / zoom, fontSize: 30 / zoom, fontWeight: 700, whiteSpace: "nowrap", boxShadow: `0 ${6 / zoom}px ${20 / zoom}px rgba(0,0,0,.4)` }}>
          {h.label}
        </div>
      )}
    </div>
  );
};

const Badge: React.FC<{ text: string; s: State }> = ({ text, s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const at = Math.round((s.duration - 2.6) * fps);
  const p = spring({ frame: f - at, fps, config: { damping: 12, stiffness: 160 } });
  if (f < at) return null;
  return (
    <div style={{ position: "absolute", right: 28, bottom: 28, display: "flex", alignItems: "center", gap: 12, padding: "12px 22px",
      background: s.verified ? "rgba(23,64,36,.92)" : "rgba(90,20,20,.92)", border: `1px solid ${s.verified ? GREEN : "#f85149"}`,
      borderRadius: 999, fontSize: 24, fontWeight: 600, transform: `scale(${p})`, boxShadow: "0 12px 40px rgba(0,0,0,.45)" }}>
      <span style={{ width: 30, height: 30, borderRadius: 99, background: s.verified ? GREEN : "#f85149", display: "grid", placeItems: "center", fontSize: 20 }}>
        {s.verified ? "✓" : "!"}
      </span>
      {text}
    </div>
  );
};

const TopBar: React.FC<{ s: State; steps: string[] }> = ({ s, steps }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f, fps, config: { damping: 20 } });
  const cur = steps.indexOf(s.label);
  return (
    <div style={{ position: "absolute", left: CARD.x, top: 26, width: CARD.w, height: 50, display: "flex", alignItems: "center", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 18px", borderRadius: 999, background: "rgba(255,255,255,.08)",
        border: "1px solid rgba(255,255,255,.12)", fontSize: 20, fontWeight: 600, opacity: p }}>
        <AppIcon app={s.app} />
        {appName(s.app)}
      </div>
      <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: -0.3, transform: `translateX(${(1 - p) * 30}px)`, opacity: p }}>
        <span style={{ color: ACCENT }}>Step {cur + 1}</span>
        <span style={{ opacity: 0.5 }}> · </span>
        {s.label}
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: "flex", gap: 8 }}>
        {steps.map((x, i) => (
          <div key={x} style={{ width: i === cur ? 46 : 14, height: 14, borderRadius: 99,
            background: i < cur ? GREEN : i === cur ? ACCENT : "rgba(255,255,255,.18)" }} />
        ))}
      </div>
    </div>
  );
};

const appName = (app: State["app"]) =>
  app === "terminal" ? "Terminal" : !app || app === "browser" ? "Browser"
    : app.replace(/^gnome-/, "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const AppIcon: React.FC<{ app: State["app"] }> = ({ app }) =>
  app && !["terminal", "browser"].includes(app) ? (
    <span style={{ width: 22, height: 18, borderRadius: 4, background: "#334155", border: "2px solid #94a3b8", display: "inline-block" }} />
  ) : app === "terminal" ? (
    <span style={{ fontFamily: MONO, fontSize: 16, background: "#0d1117", border: "1px solid #3b4250", borderRadius: 6, padding: "1px 6px", color: "#7ee787" }}>{">_"}</span>
  ) : (
    <span style={{ width: 22, height: 22, borderRadius: 99, background: "conic-gradient(#ea4335 0 33%, #fbbc05 0 66%, #34a853 0)", display: "inline-block", boxShadow: "inset 0 0 0 5px rgba(0,0,0,0)", position: "relative" }}>
      <span style={{ position: "absolute", inset: 6, borderRadius: 99, background: "#4285f4", border: "2px solid #fff" }} />
    </span>
  );

const Captions: React.FC<{ s: State }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const tt = f / fps;
  const n = [...s.narration].reverse().find((x) => tt >= x.at - 0.05);
  if (!n || !n.words.length || s.kind === "title" || s.kind === "outro") return null;
  const lt = tt - n.at;
  if (lt > n.duration + 0.4) return null;
  // lines of up to ~7 words; show the line that contains the current word
  const lines: typeof n.words[] = [];
  for (const w of n.words) {
    const last = lines[lines.length - 1];
    if (!last || last.length >= 7 || /[.?!]$/.test(last[last.length - 1].text)) lines.push([w]);
    else last.push(w);
  }
  const line = lines.find((l) => lt <= l[l.length - 1].end + 0.15) ?? lines[lines.length - 1];
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 112, display: "flex", justifyContent: "center", pointerEvents: "none" }}>
      <div style={{ padding: "14px 30px", borderRadius: 18, background: "rgba(8,9,18,.78)", backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,.1)",
        fontSize: 42, fontWeight: 800, letterSpacing: -0.5, boxShadow: "0 20px 60px rgba(0,0,0,.5)" }}>
        {line.map((w, i) => {
          const on = lt >= w.t - 0.02;
          const cur = on && lt <= w.end + 0.05;
          return (
            <span key={i} style={{ color: cur ? "#c4b5fd" : on ? "#fff" : "rgba(255,255,255,.45)", marginRight: 12, display: "inline-block", transform: `translateY(${cur ? -2 : 0}px)` }}>
              {w.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

const TitleCard: React.FC<{ t: Tutorial }> = ({ t }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const words = t.title.split(" ");
  const apps = new Set(t.states.map((s) => s.app));
  const chips = ["Linux", ...(apps.has("terminal") ? ["Terminal"] : []), ...(apps.has("browser") ? ["Browser"] : []),
    ...[...apps].filter((a): a is string => !!a && !["terminal", "browser"].includes(a)).map(appName),
    ...(t.software && !["linux", "terminal", "browser"].includes(t.software.toLowerCase()) ? [t.software] : [])];
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 34 }}>
      <div style={{ display: "flex", gap: 12, opacity: spring({ frame: f - 4, fps }) }}>
        <div style={{ padding: "8px 18px", borderRadius: 999, border: `1px solid ${ACCENT}`, color: "#c4b5fd", fontSize: 22, fontWeight: 600, letterSpacing: 3 }}>
          TUTORIAL · UNDER 2 MINUTES
        </div>
      </div>
      <div style={{ display: "flex", gap: 26, flexWrap: "wrap", justifyContent: "center", maxWidth: 1500 }}>
        {words.map((w, i) => {
          const p = spring({ frame: f - 10 - i * 5, fps, config: { damping: 14, stiffness: 140 } });
          return (
            <span key={i} style={{ fontSize: 118, fontWeight: 800, letterSpacing: -3, display: "inline-block", opacity: p,
              transform: `translateY(${(1 - p) * 60}px) rotate(${(1 - p) * 4}deg)`,
              background: i === words.length - 1 ? "linear-gradient(90deg,#a78bfa,#38bdf8)" : undefined,
              WebkitBackgroundClip: i === words.length - 1 ? "text" : undefined, color: i === words.length - 1 ? "transparent" : "#fff" }}>
              {w}
            </span>
          );
        })}
      </div>
      <div style={{ fontSize: 38, opacity: interpolate(f, [40, 60], [0, 0.85], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>{t.subtitle}</div>
      <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
        {chips.map((c, i) => {
          const p = spring({ frame: f - 55 - i * 6, fps, config: { damping: 12 } });
          return (
            <div key={c} style={{ fontFamily: MONO, fontSize: 26, padding: "10px 22px", borderRadius: 14, background: "rgba(255,255,255,.07)",
              border: "1px solid rgba(255,255,255,.14)", transform: `scale(${p})` }}>{c}</div>
          );
        })}
      </div>
      {t.teaser && <Terminal3D f={f} cmd={t.teaser} />}
    </AbsoluteFill>
  );
};

const Terminal3D: React.FC<{ f: number; cmd: string }> = ({ f, cmd }) => {
  const n = Math.max(0, Math.floor((f - 80) / 2));
  return (
    <div style={{ marginTop: 30, fontFamily: MONO, fontSize: 30, padding: "18px 30px", minWidth: 640, borderRadius: 16, background: "#0d1117",
      border: "1px solid #30363d", color: "#7ee787", opacity: interpolate(f, [70, 85], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
      transform: `perspective(900px) rotateX(${interpolate(f, [70, 110], [18, 6], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })}deg)`,
      boxShadow: `0 30px 80px rgba(124,92,255,.25)` }}>
      {cmd.slice(0, n)}
      <span style={{ opacity: Math.floor(f / 15) % 2 ? 1 : 0, color: "#58a6ff" }}>▍</span>
    </div>
  );
};

const Outro: React.FC<{ t: Tutorial }> = ({ t }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const items = t.recap;
  const head = t.done.split(" ");
  const big = items.length <= 3;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 44 }}>
      <div style={{ fontSize: 96, fontWeight: 800, letterSpacing: -2, opacity: spring({ frame: f, fps }), transform: `scale(${0.9 + 0.1 * spring({ frame: f, fps })})` }}>
        {head.slice(0, -1).join(" ")}{" "}
        <span style={{ background: "linear-gradient(90deg,#a78bfa,#38bdf8)", WebkitBackgroundClip: "text", color: "transparent" }}>{head[head.length - 1]}</span>
      </div>
      <div style={{ display: "flex", gap: big ? 30 : 22, flexWrap: "wrap", justifyContent: "center", maxWidth: 1760 }}>
        {items.map((x, i) => {
          const p = spring({ frame: f - 25 - i * 22, fps, config: { damping: 11, stiffness: 150 } });
          return (
            <div key={x} style={{ display: "flex", alignItems: "center", gap: 16, padding: "20px 34px", borderRadius: 20, background: "rgba(255,255,255,.07)",
              border: "1px solid rgba(255,255,255,.14)", fontSize: big ? 44 : 34, fontWeight: 700, transform: `scale(${p})`, opacity: p }}>
              <span style={{ width: 52, height: 52, borderRadius: 99, background: GREEN, display: "grid", placeItems: "center", fontSize: 32 }}>✓</span>
              {x}
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 30, opacity: interpolate(f, [100, 125], [0, 0.7], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>
        Recorded live on a real Linux machine · made with <b>Cueframe</b>
      </div>
    </AbsoluteFill>
  );
};
