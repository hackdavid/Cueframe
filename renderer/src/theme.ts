// Shared theme + motion presets. Every component (overlays, highlight kit, cards)
// imports from here so the whole video moves and looks like one system. Tutorial.tsx
// still carries its own constants; they converge here as surfaces are touched.
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

const { fontFamily: SANS } = loadInter("normal", { weights: ["400", "600", "800"], subsets: ["latin"] });
const { fontFamily: MONO } = loadMono("normal", { weights: ["500"], subsets: ["latin"] });

export { SANS, MONO };

export const SRC_W = 1920;
export const SRC_H = 1080;
// The recording is shown in a card; the card never changes size.
export const CARD = { x: 150, y: 96, w: 1620, h: 911 };
export const K = CARD.w / SRC_W;

export const ACCENT = "#7c5cff";
export const ACCENT_SOFT = "#c4b5fd";
export const GREEN = "#3fb950";
export const PANEL = "rgba(255,255,255,.07)";
export const PANEL_BORDER = "rgba(255,255,255,.14)";
export const SHADOW = "0 20px 60px rgba(0,0,0,.5)";

export const SPRING_POP = { damping: 12, stiffness: 160 } as const;
export const SPRING_SOFT = { damping: 18, stiffness: 120 } as const;
