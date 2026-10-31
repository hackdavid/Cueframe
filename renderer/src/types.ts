// Mirrors app/tutorial/contracts.py (the state graph); built by scripts/build_tutorial.py.
export type Word = { text: string; t: number; end: number };
export type Narration = { text: string; audio_url: string | null; duration: number; at: number; words: Word[] };
export type Camera = { t: number; zoom: number; cx: number; cy: number };
export type Highlight = {
  t_in: number; t_out: number; x: number; y: number; w: number; h: number;
  label: string | null;
  kind?: "box" | "spotlight" | "callout"; // default "box"
  dim?: number; // spotlight/callout: dimmed surround opacity (0-0.75)
  cursor?: boolean; // a pointer glides to the box centre, click ripple on arrival
  label_at?: "auto" | "top" | "bottom" | "left" | "right";
};
export type Sfx = { t: number; kind: "click" | "keys" | "whoosh" | "success" | "pop"; duration?: number };
// A motion component on a state; props are resolved server-side against verified facts.
export type OverlaySpec = {
  id: string;
  component: string;
  t_in: number; // seconds into the state
  t_out: number; // -1 = until the state's end minus a beat
  props: Record<string, unknown>;
};
export type State = {
  id: string;
  kind: "title" | "action" | "wait" | "outro";
  app: string | null; // "browser", "terminal" or a desktop app name
  label: string;
  narration: Narration[];
  source: { t_in: number; t_out: number; rate: number } | null;
  camera: Camera[];
  highlights: Highlight[];
  sfx: Sfx[];
  overlays: OverlaySpec[];
  media_url?: string | null; // a fully rendered clip (a board segment) instead of source
  verified: boolean;
  badge: string | null;
  duration: number;
};
export type Tutorial = {
  id: string;
  title: string;
  subtitle: string;
  width: number;
  height: number;
  fps: number;
  recording_url: string | null;
  music_url: string | null;
  steps: string[];
  software: string;
  kicker?: string;
  teaser: string;
  done: string;
  recap: string[];
  states: State[];
};
