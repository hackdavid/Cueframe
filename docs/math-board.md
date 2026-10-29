# Math board tutor

A math question in chat ("solve 3x − 5 = 16", "find the area under x eˣ from 0 to 2",
"prove Pythagoras") becomes a narrated board lesson. Nothing is recorded from a browser:
the board is drawn from a plan in which every object has known coordinates.

```
chat → level question (child / high school / undergrad, unless the message says)
     → BoardPlan (app/board/contracts.py, skill app/skills/tutor.md)
     → structural checks + SymPy checks (app/board/checks.py, own process, 20 s per check)
     → second-teacher review → repair loop (max 4 plans, each trace posted in chat)
     → sandbox: typeset every op (LaTeX errors are repaired), ElevenLabs voice per step
     → timeline from word timings (app/board/timeline.py) → parallel render
     → narration mix, captions, thumbnail → R2 → video in chat
```

## Board plan
Steps hold `say` (spoken) and `ops`. Each op is semantic: `write` (LaTeX row, can morph
from an earlier row), `text`, `section`, `box`/`underline`/`cross`/`arrow` on earlier
rows, `figure` (axes for graphs, equal-scale plane for geometry) and the figure ops
`plot`, `area`, `riemann`, `tangent`, `point`, `segment`, `polygon`, `circle`, `angle`,
`label`. Coordinates are math units; `app/board/layout.py` maps them to the board.
An op starts when its `cue` word is spoken.

## Infinite board, fixed 16:9 camera
Rows stack downward forever; a figure sits to the right of the rows written beside it.
The camera frame is always 16:9 (1920×1080) and only moves vertically, just enough to
keep the active row or figure above the captions. Nothing is ever shrunk to fit.

## Checks
`equal`, `equivalent`, `solves` (exact solution set), `derivative`, `antiderivative`,
`definite`, `limit`, `value` and `geometry` (dist, angle, area, slope on the drawn
points). Expressions are parsed from a whitelist (no attributes, no dunders).
SymPy proves the algebra; the review pass catches valid algebra that teaches the wrong
method or skips a step the level needs.

## Running it
`python -m app.board.engine check|render JOB [PARTS]` runs anywhere Manim, LaTeX and
SymPy are installed. In production it runs in a headless Modal image (`board_image`,
code mounted at start), using the normal sandbox limits and cleanup. Parts render in
parallel (one per CPU) and are joined without re-encoding; all timings are whole frames.
