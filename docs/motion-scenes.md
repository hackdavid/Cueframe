# Motion scenes: one video, three scene types

A tutorial/reel/explainer is a sequence of states in one graph (`app/tutorial/contracts.py`),
composited in Remotion. Three scene types mix freely:

- **clip** — the real sandbox recording (browser, terminal), with highlights snapped to
  real DOM coordinates: a callout box with a label, a dimmed surround (`spotlight`),
  and a pointer that glides to the element and clicks (`cursor`). Set in
  `app/tutorial/build.py::plan_step` from the sandbox's measured element boxes.
- **overlay** — a motion component with no footage (`star_counter`, `contrib_graph`,
  `logo_wall`, `stat_chip`, `claim_card`), registered in
  `app/tutorial/components.py` + one React file in `renderer/src/components/`. Props
  carry numbers only by `fact_key` into the job's FACTS (`app/tutorial/facts.py`,
  fetched from the GitHub/HF APIs, cached 24 h, stored on the spec); `resolve()` inlines
  the verified values before render, so a number the source never stated can't appear.
- **board** — a concept diagram rendered by the Manim board pipeline
  (`app/board/concept.py::scene` → `app/board/tutor.py::render_segment`) and embedded as
  a muted clip (`State.media_url`); its own narration is staged as
  `audio/board_<step>_<i>.mp3` and laid in by the normal audio mix.

The planner emits scenes as steps with `kind: "overlay" | "board"` (no sandbox action);
`problems()` validates them against the registry and the FACTS, `capture`/`rehearse`
skip them (a synthetic record keeps the one-record-per-step contract), and
`build.py::build` turns them into states. Papers, models, repos and PDFs all go through
this one pipeline; `concept.make` remains for direct board requests, and the math tutor
path is untouched.

## Adding a component

1. `app/tutorial/components.py`: a props schema + `@register("name", Schema, doc, seconds)`
   validator (facts-bound checks; the doc line feeds the planner prompt).
2. `renderer/src/components/Name.tsx` + one line in `registry.ts`.
Nothing else changes: layout, timing, staging and caching are generic.

## Narration dedup

`app/tutorial/dedup.py` flags two lines that say the same thing (normalized exact match,
or content-word Jaccard ≥ 0.75 with a 4-token floor). Always-on guards keep rewrites
(`repair`, `shown.check`, `stretch`) from introducing repeats; blocking checks (plan
validation, chat edits) activate with `CUEFRAME_DEDUP_ENFORCE=1`.

## Notebook format (designed, not built)

JupyterLab in the desktop sandbox: pip layer in `desktop_image`, launch with a terminal
step (`jupyter lab --no-browser --port=8888 --ServerApp.token='' &`), `open_url` the
local page, then a new `cell` step kind (type the code, `shift+Return`) — cells are real
DOM, so the existing find/type/key machinery works; `expect` needs a longer poll for
slow cells. Touch points: `app/sandbox/contracts.py` (kind), `app/sandbox/base.py`
(`_act`), `app/sandbox/modal_box.py` (image), `planner.py` (kind, prompt, checks),
`build.py` (edit), plus a `app/skills/notebook.md` content skill.
