import asyncio
import subprocess

from app.heal import Healer
from app.tutorial import planner, shown


class _LLM:
    configured = True

    def __init__(self, fixes):
        self.fixes, self.calls = fixes, []

    async def parse(self, schema, **kw):
        self.calls.append(kw)
        return schema(fixes=self.fixes), None


class _Ctx:
    def __init__(self, llm):
        self.llm, self.notes = llm, []

        class S:
            research_model = "m"

        self.settings = S()

    def thinking(self, title, text):
        self.notes.append(text)

    def emit(self, *a, **kw):
        pass


def _video(tmp_path):
    path = tmp_path / "rec.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x180:d=4", str(path)], check=True)  # fmt: skip
    return path


def _spec():
    return {
        "steps": [
            {"id": "open", "label": "Open repo", "narration": ["open"]},
            {"id": "roles", "label": "Read roles", "narration": ["roles"]},
        ],
        "lines": {"open": "Here is the repo page.",
                  "roles": "It has three roles: Creator, Explorer and Evolver."},
    }  # fmt: skip


RECS = [
    {"step": "open", "in_video": True, "ok": True, "t_in": 0.0, "t_out": 2.0},
    {"step": "roles", "in_video": True, "ok": True, "t_in": 2.0, "t_out": 4.0},
]


def test_rewrites_lines_the_frames_dont_show(tmp_path):
    fix = shown.LineFix(key="roles", problem="the three roles",
                        narration="Scroll down and the README opens up.")  # fmt: skip
    llm = _LLM([fix])
    ctx, spec = _Ctx(llm), _spec()
    heal = Healer(ctx)
    changed = asyncio.run(shown.check(ctx, spec, RECS, _video(tmp_path), heal))
    assert changed == ["roles"]
    assert spec["lines"]["roles"] == "Scroll down and the README opens up."
    assert spec["lines"]["open"] == "Here is the repo page."
    images = [c for c in llm.calls[0]["input"][0]["content"] if c["type"] == "input_image"]
    assert len(images) == 4  # two frames per step, cut from the real recording
    assert "Narration check" in heal.log[0]


def test_ignores_invalid_rewrites(tmp_path):
    long = " ".join(["word"] * 30)
    fixes = [shown.LineFix(key="nope", problem="x", narration="Hi."),
             shown.LineFix(key="open", problem="x", narration=long)]  # fmt: skip
    ctx, spec = _Ctx(_LLM(fixes)), _spec()
    assert asyncio.run(shown.check(ctx, spec, RECS, _video(tmp_path), Healer(ctx))) == []
    assert spec["lines"] == _spec()["lines"]


def test_skipped_without_a_model(tmp_path):
    ctx = _Ctx(None)
    assert asyncio.run(shown.check(ctx, _spec(), RECS, _video(tmp_path), Healer(ctx))) == []


def test_wide_output_zooms_out_so_lines_fit():
    assert planner.zoom_fit(20) == 1.8
    assert planner.zoom_fit(80) < 1.4
    assert planner.zoom_fit(200) == 1.0


def test_long_code_lines_are_rejected():
    from tests.test_planner import _plan, _step

    long = "printf '%s\\n' 'x = 1' '" + "y" * 90 + "' > /work/a.py"
    ok = "printf '%s\\n' 'x = 1' 'print(x)' > /work/a.py"
    bad = planner.problems(_plan([_step("write", command=long), _step("run")]))
    assert any("at most 72 characters" in p for p in bad)
    good = planner.problems(_plan([_step("write", command=ok), _step("run")]))
    assert not any("characters" in p for p in good)
