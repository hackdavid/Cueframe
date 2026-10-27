import { Composition } from "remotion";
import { TutorialVideo, framesOf } from "./Tutorial";
import type { Tutorial } from "./types";

const empty: Tutorial = {
  id: "empty", title: "Cueframe", subtitle: "", width: 1920, height: 1080, fps: 30,
  recording_url: null, music_url: null, steps: [], states: [],
};

export const Root: React.FC = () => (
  <Composition
    id="Tutorial"
    component={TutorialVideo}
    defaultProps={{ tutorial: empty }}
    width={1920}
    height={1080}
    fps={30}
    durationInFrames={30}
    calculateMetadata={({ props }) => {
      const t = props.tutorial;
      const total = t.states.reduce((n, s) => n + framesOf(s, t.fps), 0);
      return { durationInFrames: Math.max(1, total), fps: t.fps, width: t.width, height: t.height };
    }}
  />
);
