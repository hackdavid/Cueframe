// Mirrors app/tutorial/contracts.py (the state graph); built by scripts/build_tutorial.py.
export type Word = { text: string; t: number; end: number };
export type Narration = { text: string; audio_url: string | null; duration: number; at: number; words: Word[] };
export type Camera = { t: number; zoom: number; cx: number; cy: number };
export type Highlight = { t_in: number; t_out: number; x: number; y: number; w: number; h: number; label: string | null };
export type Sfx = { t: number; kind: "click" | "keys" | "whoosh" | "success" | "pop"; duration?: number };
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
