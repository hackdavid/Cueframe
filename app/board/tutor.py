"""Math tutor jobs: chat -> board plan -> SymPy checks -> voice -> board render -> R2.
The board is drawn, never recorded: it renders in a headless sandbox (or on this server
with CUEFRAME_BOARD_RUNNER=local) and the narration is mixed in here."""

import asyncio
import base64
import dataclasses
import json
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app import endcard, languages, proc
from app.board import timeline
from app.board.contracts import BoardPlan, Level, normalize, problems
from app.jobs import JobContext, JobFailed
from app.tutorial import formats
from app.tutorial.voice import conversation_voice, speak

LEVELS: dict[str, Level] = {
    "Child (primary school)": "child",
    "High school": "school",
    "University / undergrad": "undergrad",
}
TRIES = 4
REMOTE = "/work/job"


def skill() -> str:
    return (formats.SKILLS / "tutor.md").read_text().strip()


class Review(BaseModel):
    """LLM contract: a second teacher's review of a checked plan."""

    ok: bool = Field(description="True if a student at this level would learn it correctly")
    issues: list[str] = Field(
        description="Each real problem: a wrong or unjustified method, a skipped step "
        "the level needs, narration that doesn't match the board, a figure that doesn't "
        "show what is said; [] if ok"
    )


class Question(BaseModel):
    """LLM contract: the one math question in a student's photo."""

    found: bool = Field(description="True if the photo shows a math question")
    question: str = Field(description="That question written out exactly in plain text "
                          "(^ for powers, / for fractions), '' if none")  # fmt: skip
    reason: str = Field("", description="Why not, when found is false")


async def from_photo(ctx: JobContext, text: str) -> str:
    """If the message has a photo, read the single question in it and teach that."""
    imgs = [a for a in ctx.input.get("attachments") or []
            if (a.get("content_type") or "").startswith("image/")]  # fmt: skip
    if not imgs:
        return text
    a = imgs[0]
    data = (ctx.settings.uploads_dir / a["id"] / a["filename"]).read_bytes()
    url = f"data:{a['content_type']};base64,{base64.b64encode(data).decode()}"
    ctx.thinking("Tutor", "Reading the question in your photo")
    q, _ = await ctx.llm.parse(
        Question, purpose="board.photo", model=ctx.settings.research_model,
        instructions="Read the one math question in the student's photo. Copy it exactly; "
        "don't solve it. If there are several, take the first.",
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": f"Student's message: {text}"},
            {"type": "input_image", "image_url": url}]}],
    )  # fmt: skip
    if not q.found or not q.question.strip():
        raise JobFailed(f"I couldn't read a math question in the photo: {q.reason}", {})
    ctx.reply(f"**The question in your photo:** {q.question}")
    return f"{text}\n\nThe question, read from the student's photo: {q.question}"


async def clarify(ctx: JobContext, level: Level | None) -> Level:
    if level:
        return level
    ans = await ctx.ask("Who is this lesson for?", list(LEVELS), key="level")
    return LEVELS.get(ans, "school")


async def _sympy(plan: BoardPlan, timeout: float = 240) -> tuple[int, list[str]]:
    """Run the checks in a child process (each is bounded; the whole run is killed if
    it still overruns). Blocking subprocess in a thread: asyncio subprocesses raise
    NotImplementedError on uvicorn's Windows SelectorEventLoop."""
    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-m", "app.board.checks"], input=plan.model_dump_json().encode(),
            capture_output=True, timeout=timeout,
        )

    try:
        p = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return sum(len(s.checks) for s in plan.steps), [f"the checks took over {timeout:.0f} s"]
    if p.returncode:
        raise RuntimeError(f"math checks crashed: {p.stderr.decode()[-500:]}")
    res = json.loads(p.stdout)
    return res["checks"], res["failed"]


async def _checked(plan: BoardPlan, need: bool = True) -> tuple[int, list[str]]:
    bad = problems(plan)
    n, failed = await _sympy(plan)
    if not n and need:
        bad.append("there are no checks: every step that changes an equation or computes a "
                   "value needs one")  # fmt: skip
    return n, bad + failed


async def plan(ctx: JobContext, problem: str, level: Level, seconds: int | None,
               language: str | None = None) -> BoardPlan:  # fmt: skip
    want = (f"Aim for about {seconds} s of narration in total." if seconds
            else "Take as long as the problem needs; don't pad.")  # fmt: skip
    base = f"Level: {level}\n{want}\nProblem from the student:\n{problem}"
    if language:  # board writing stays in English/LaTeX; only the voice changes language
        base += languages.instruction(language) + (
            " On the board itself write only maths, LaTeX and short English labels."
        )
    feedback, reviewed = "", False
    prev: BoardPlan | None = None
    for attempt in range(1, TRIES + 1):
        ctx.thinking("Tutor", "Planning the board" if attempt == 1 else "Fixing the plan")
        p, _ = await ctx.llm.parse(
            BoardPlan, purpose="board.plan", model=ctx.settings.research_model,
            instructions=skill(), input=base + feedback, reasoning_effort="medium",
        )  # fmt: skip
        if not p.feasible:
            raise JobFailed(f"I can't teach that on the board: {p.reason}", {"planned": False})
        p.level = level
        if prev is not None:
            keep_checks(prev, p)
        prev = p
        n, issues = await _checked(normalize(p))
        if not issues and not reviewed:
            ctx.thinking("Reviewer", "A second teacher is reviewing the method")
            r, _ = await ctx.llm.parse(
                Review, purpose="board.review", model=ctx.settings.research_model,
                instructions="You review a board lesson another teacher planned. Be strict "
                "about correctness and the level; ignore style.",
                input=f"{base}\n\nPlan:\n{p.model_dump_json()}", reasoning_effort="low",
            )  # fmt: skip
            reviewed = True
            issues = [] if r.ok else [f"review: {i}" for i in r.issues]
        ctx.reply(_trace(attempt, len(p.steps), n, issues))
        if not issues:
            return p
        feedback = (f"\n\nYour previous plan:\n{p.model_dump_json()}\n\nIt has these problems. "
                    "Return the whole plan again with only these fixed; keep every other "
                    "step, op and check as it is (checks are mandatory):\n- "
                    + "\n- ".join(issues))  # fmt: skip
    raise JobFailed(f"The plan still had problems after {TRIES} tries: {'; '.join(issues)}",
                    {"planned": False, "issues": issues})  # fmt: skip


def keep_checks(prev: BoardPlan, new: BoardPlan) -> int:
    """Repairs sometimes drop checks: put back the previous plan's checks on steps that
    came back without any (they are verified again). Returns how many steps got them."""
    if len(prev.steps) != len(new.steps):
        return 0
    n = 0
    for old, step in zip(prev.steps, new.steps, strict=True):
        if old.checks and not step.checks:
            step.checks = [c.model_copy() for c in old.checks]
            n += 1
    return n


def _trace(attempt: int, steps: int, n: int, issues: list[str]) -> str:
    head = f"**Plan {attempt}:** {steps} steps, {n} SymPy checks"
    if not issues:
        return f"{head}: all passed."
    return f"{head}: {len(issues)} problem(s), sending it back:\n" + "\n".join(
        f"- {i}" for i in issues[:12]
    )


# -- rendering --------------------------------------------------------------------------
class Runner:
    """Runs the board engine where it lives: a headless sandbox, or this server."""

    def __init__(self, box: Any = None, local: Path | None = None):
        self.box, self.local = box, local

    async def put(self, name: str, data: bytes) -> None:
        if self.box is not None:
            await self.box.write_file(f"{REMOTE}/{name}", data)
        else:
            (self.local / name).write_bytes(data)  # type: ignore[operator]

    async def run(self, cmd: str, timeout: float, *args: str) -> dict[str, Any]:
        if self.box is not None:
            res = await self.box.exec(
                f"cd /opt/board && python -m app.board.engine {cmd} {REMOTE} {' '.join(args)}"
                " 2>/tmp/board.err;"
                " tail -c 3000 /tmp/board.err >&2", timeout,
            )  # fmt: skip
            out = res.stdout
        else:
            try:
                _, data = await proc.raw(
                    sys.executable, "-m", "app.board.engine", cmd, str(self.local), *args,
                    timeout=timeout,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                raise TimeoutError(f"board engine {cmd} exceeded {timeout:.0f}s") from None
            out = data.decode()
        lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
        if not lines:
            raise RuntimeError(f"board engine gave no result: {out[-500:]}")
        return json.loads(lines[-1])

    async def get(self, name: str) -> bytes:
        if self.box is not None:
            return await self.box.read_file(f"{REMOTE}/{name}")
        return (self.local / name).read_bytes()  # type: ignore[operator]


_RANK = re.compile(r"#(\d+)")  # "#1" is read out as "one j one"
# A number as written, not one inside a name like "V4.1": "3,947", "90.20", "48.7%".
_NUM = re.compile(r"(?<![\w.,-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\.?\d|,\d)(%?)")
_SYMBOLS = {"≥": " at least ", ">=": " at least ", "≤": " at most ", "<=": " at most ", "_": " "}
_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _words(n: int) -> str:
    """3947 as "three thousand nine hundred forty-seven"."""
    for scale, name in (
        (10**9, "billion"),
        (10**6, "million"),
        (1000, "thousand"),
        (100, "hundred"),
    ):
        if n >= scale:
            rest = n % scale
            return f"{_words(n // scale)} {name}" + (f" {_words(rest)}" if rest else "")
    return _ONES[n] if n < 20 else _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")


def _spoken(say: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """What the voice reads: "#1" as "number 1", "3,947" in words (digits get misread) and
    "90.20%" digit by digit ("90 point 2 0 percent"). Also each changed number with its
    spoken words, so captions can show it as written again."""
    reps: list[tuple[str, list[str]]] = []

    def say_num(m: re.Match) -> str:
        whole, dec, pct = m[1], m[2], m[3]
        out = _words(int(whole.replace(",", ""))) if "," in whole else whole
        if dec:
            out += " point " + " ".join(dec)
        if pct:
            out += " percent"
        if out != m[0]:
            reps.append((m[0], out.lower().split()))
        return out

    for sym, words in _SYMBOLS.items():
        say = say.replace(sym, words)
    say = _RANK.sub(r"number \1", " ".join(say.split()))
    return _NUM.sub(say_num, say), reps


def _rejoin(words: list[dict], reps: list[tuple[str, list[str]]]) -> list[dict]:
    """Caption words for "90 point 2 0 percent" back as one "90.20%", timed over all of them."""
    out, i, left = [], 0, list(reps)
    while i < len(words):
        for k, (text, toks) in enumerate(left):
            got = words[i : i + len(toks)]
            said = [w["text"].lower() for w in got]
            if len(got) == len(toks) and said[:-1] == toks[:-1] and said[-1].startswith(toks[-1]):
                tail = got[-1]["text"][len(toks[-1]) :]  # "5x," keeps "x,"
                out.append({**got[0], "text": text + tail, "end": got[-1]["end"]})
                del left[: k + 1]
                i += len(toks)
                break
        else:
            out.append(words[i])
            i += 1
    return out


Fit = Callable[[BoardPlan, dict], Awaitable[BoardPlan | None]]  # re-narrate to the asked length


async def _voice(p: BoardPlan, work: Path, voice_id: str | None = None,
                 ) -> list[tuple[float, list[dict]]]:  # fmt: skip
    sem = asyncio.Semaphore(4)

    async def one(i: int, say: str) -> tuple[float, list[dict]]:
        async with sem:
            spoken, reps = _spoken(say)
            audio, words = await speak(spoken, voice_id=voice_id)
        (work / f"s{i}.mp3").write_bytes(audio)
        ws = _rejoin([w.model_dump() for w in words], reps)
        return (ws[-1]["end"] + 0.1 if ws else 1.0), ws

    return list(await asyncio.gather(*(one(i, s.say) for i, s in enumerate(p.steps))))


async def _board(ctx: JobContext, run: Runner, p: BoardPlan, problem: str, work: Path,
                 voice_id: str | None = None, rules: str | None = None,
                 validate: Callable[[BoardPlan], list[str]] | None = None,
                 fit: Fit | None = None) -> tuple[BoardPlan, dict, dict, dict]:  # fmt: skip
    """LaTeX check (repairing the plan from the errors), voice, then the render. The
    voice is recorded while the board is typeset; only a repaired plan re-records it."""
    voicing, voiced_for = asyncio.create_task(_voice(p, work, voice_id)), p
    for attempt in range(3):
        await run.put("plan.json", p.model_dump_json().encode())
        ctx.phase("check", "Typesetting every line of the board")
        chk = await run.run("check", 600)
        if chk.get("error"):
            raise RuntimeError(chk["error"])
        if not chk["errors"]:
            break
        errs = [f"step {e['step']} op {e['op']} ({e['kind']} {e['latex']!r}): {e['error']}"
                for e in chk["errors"]]  # fmt: skip
        ctx.reply("**The board couldn't draw some lines:**\n" + "\n".join(f"- {e}" for e in errs))
        if attempt == 2:
            raise JobFailed("Some board lines still couldn't be drawn", {"errors": errs})
        fixed, _ = await ctx.llm.parse(
            BoardPlan, purpose="board.fix", model=ctx.settings.research_model,
            instructions=rules or skill(), reasoning_effort="low",
            input=f"Plan:\n{p.model_dump_json()}\n\nThese ops failed to render; return the "
            "whole plan with only those ops fixed:\n- " + "\n- ".join(errs),
        )  # fmt: skip
        fixed.level = p.level
        keep_checks(p, fixed)
        n, issues = await _checked(normalize(fixed), need=any(s.checks for s in p.steps))
        if validate:
            issues += validate(fixed)
        if issues:
            raise JobFailed(f"The fixed plan failed its checks: {'; '.join(issues)}",
                            {"issues": issues})  # fmt: skip
        p = fixed
    if voiced_for is not p:
        voicing.cancel()
        voicing = asyncio.create_task(_voice(p, work, voice_id))
    ctx.phase("voice", "Recording the teacher's voice")
    voiced = await voicing
    tl = timeline.build(p, voiced)
    if fit and (fitted := await fit(p, tl)):
        ctx.phase("voice", "Re-recording the longer narration")
        p = fitted
        tl = timeline.build(p, await _voice(p, work, voice_id))
        await run.put("plan.json", p.model_dump_json().encode())
    (work / "timeline.json").write_text(json.dumps(tl))
    await run.put("timeline.json", json.dumps(tl).encode())
    if run.box is not None:
        # Final say in the sandbox: its pinned SymPy re-verifies the exact plan it is
        # about to draw, so a server/sandbox version drift fails here, not mid-render.
        ctx.phase("check", "Verifying the maths in the sandbox")
        ver = await run.box.exec(
            f"cd /opt/board && python -m app.board.checks < {REMOTE}/plan.json", 300
        )
        rows = [ln for ln in ver.stdout.splitlines() if ln.startswith("{")]
        if ver.returncode or not rows:
            raise RuntimeError(f"sandbox maths check crashed: {ver.stderr[-400:]}")
        failed = json.loads(rows[-1])["failed"]
        if failed:
            raise JobFailed("The sandbox maths check failed: " + "; ".join(failed[:5]),
                            {"issues": failed})  # fmt: skip
    ctx.phase("render", f"Drawing the board ({tl['seconds']:.0f} s of video)")
    res = await run.run("render", 1800, str(int(ctx.settings.board_cpu)))
    if res.get("error"):
        raise RuntimeError(res["error"])
    (work / "board.mp4").write_bytes(await run.get("board.mp4"))
    return p, tl, chk, res


def _mix(work: Path, tl: dict, n: int) -> Path:
    """Narration at each step's start over the silent board video; thumbnail; captions."""
    out = work / "tutor.mp4"
    ins: list[str] = ["-i", str(work / "board.mp4")]
    chains = []
    for i, st in enumerate(tl["steps"][:n]):
        ins += ["-i", str(work / f"s{i}.mp3")]
        ms = round(st["start"] * 1000)
        chains.append(f"[{i + 1}:a]adelay={ms}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(n))
    graph = ";".join(chains) + f";{mix}amix=inputs={n}:normalize=0,loudnorm=I=-16:TP=-1.5,apad[a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", graph,
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a",
                    "192k", "-t", f"{tl['seconds']:.3f}", str(out)], check=True)  # fmt: skip
    at = f"{tl['seconds'] - 0.6:.2f}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", at, "-i", str(out),
                    "-frames:v", "1", str(work / "thumbnail.png")], check=True)  # fmt: skip
    (work / "captions.srt").write_text(timeline.srt(timeline.captions(tl)))
    return out


async def make(ctx: JobContext, problem: str, level: Level | None = None,
               seconds: int | None = None) -> dict[str, Any]:  # fmt: skip
    """A board lesson; any failure is also explained in the chat, not just on the job."""
    try:
        return await _make(ctx, problem, level, seconds)
    except Exception as exc:
        ctx.reply(f"**I couldn't make this lesson:** {exc}")
        raise


async def _make(ctx: JobContext, problem: str, level: Level | None = None,
               seconds: int | None = None) -> dict[str, Any]:  # fmt: skip
    import os

    missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Board narration needs {', '.join(missing)} on the server")
    conv = ctx.store.get_conversation(ctx.conversation_id) if ctx.conversation_id else None
    if not conv or ctx.llm is None or not ctx.llm.configured:
        raise ValueError("board lessons need a conversation and a model")
    t0 = time.monotonic()
    language = (ctx.input.get("job_input") or {}).get("language") or languages.detect(problem)
    problem = await from_photo(ctx, problem)
    level = await clarify(ctx, level)
    ctx.phase("plan", "Planning the board")
    if language:
        ctx.reply(f"I'll teach this in **{languages.name(language)}**; the board stays in "
                  "maths notation.")  # fmt: skip
    p = await plan(ctx, problem, level, seconds, language)
    ctx.reply(f"**{p.title}**: here's the board plan ({level}).\n\n" + describe(p))
    return await deliver(ctx, conv, p, problem, t0, language)


async def deliver(ctx: JobContext, conv: dict, p: BoardPlan, problem: str, t0: float,
                  language: str | None, recipe: str = "board", rules: str | None = None,
                  validate: Callable[[BoardPlan], list[str]] | None = None,
                  fit: Fit | None = None) -> dict[str, Any]:  # fmt: skip
    """Typeset, voice and render a checked board plan, then post the video in chat."""
    # the conversation's narrator pick wins over the per-language voice
    vid = conversation_voice(ctx) or languages.voice_id(language)
    extra = {k: v for k, v in {"rules": rules, "validate": validate, "fit": fit}.items() if v}
    row = ctx.store.create_tutorial(conv["user_id"], conv["id"], ctx.id, recipe, p.title,
                                    {"board": p.model_dump(), "problem": problem})  # fmt: skip
    tid = row["id"]
    ctx.store.update_tutorial(tid, active_job=ctx.id, status="rendering")
    work = ctx.settings.data_dir / "board" / tid
    work.mkdir(parents=True, exist_ok=True)
    if ctx.settings.board_runner == "local":
        p, tl, chk, res = await _board(ctx, Runner(local=work), p, problem, work, vid, **extra)
    else:
        mgr = ctx._manager.sandboxes
        cfg = dataclasses.replace(ctx.settings, sandbox_image="board",
                                  sandbox_cpu=ctx.settings.board_cpu, sandbox_memory_mb=8192,
                                  sandbox_max_seconds=2400)  # fmt: skip
        ctx.phase("sandbox", "Starting the board renderer")
        async with mgr.session(ctx.id, ctx.conversation_id, settings=cfg) as (sid, box):  # noqa: SIM117
            async with mgr.busy(sid):
                p, tl, chk, res = await _board(ctx, Runner(box=box), p, problem, work, vid, **extra)
    ctx.phase("mix", "Adding the voice and captions")
    video = await asyncio.to_thread(_mix, work, tl, len(p.steps))
    video = await endcard.brand(ctx, video)
    storage = ctx._manager.storage
    up = {
        "video": await storage.put(f"tutorials/{tid}/video-v1.mp4", video.read_bytes(),
                                   "video/mp4"),
        "captions": await storage.put(f"tutorials/{tid}/captions-v1.srt",
                                      (work / "captions.srt").read_bytes(), "application/x-subrip"),
        "thumbnail": await storage.put(f"tutorials/{tid}/thumbnail-v1.png",
                                       (work / "thumbnail.png").read_bytes(), "image/png"),
    }  # fmt: skip
    n_checks = sum(len(s.checks) for s in p.steps)
    stats = {"duration": tl["seconds"], "video_seconds": res["seconds"], "steps": len(p.steps),
             "checks": n_checks, "board_height": chk["board_height"],
             "camera_moves": chk["camera_moves"], "render_seconds": res["render_seconds"],
             "parts": res["parts"], "total_seconds": round(time.monotonic() - t0, 1)}  # fmt: skip
    output = {**up, "stats": stats, "job_id": ctx.id, "language": language or "en"}
    ctx.store.update_tutorial(tid, status="ready", version=1, output=output,
                              spec={"board": p.model_dump(), "problem": problem})  # fmt: skip
    ctx.emit("tutorial.ready", {"tutorial_id": tid, "version": 1, "video_url": up["video"]["url"]})
    ctx.reply(
        f"**Your lesson is ready: {p.title}** ({tl['seconds']:.0f} s, 1080p)\n\n"
        f'<video src="{up["video"]["url"]}" poster="{up["thumbnail"]["url"]}" controls></video>'
        f"\n\n[Captions (.srt)]({up['captions']['url']}) · [Thumbnail]({up['thumbnail']['url']})"
        f" · [Get a share link](/share/{tid})"
        f"\n\n{len(p.steps)} steps, {n_checks} SymPy checks passed. The board is "
        f"{chk['board_height']:.1f} units tall (one screen is 8); the camera scrolled "
        f"{chk['camera_moves'] - 1} times. Rendered in {res['render_seconds']:.0f} s "
        f"({res['parts']} parts in parallel).\n\n"
        "| # | Step | Checks |\n|---|---|---|\n"
        + "\n".join(
            f"| {i} | {s.say[:70]}{'…' if len(s.say) > 70 else ''} | "
            f"{', '.join(c.kind for c in s.checks) or '—'} |"
            for i, s in enumerate(p.steps, 1)
        )  # fmt: skip
    )
    return {"tutorial_id": tid, "version": 1, **output}


def describe(p: BoardPlan) -> str:
    lines = []
    for i, s in enumerate(p.steps, 1):
        shown = [o.latex for o in s.ops if o.kind == "write" and o.latex][:2]
        figs = sorted({o.kind for o in s.ops if o.kind not in ("write", "text", "section")})
        bits = [f"`{x}`" for x in shown] + ([f"draws {', '.join(figs)}"] if figs else [])
        lines.append(f"{i}. {s.say}" + (f" ({'; '.join(bits)})" if bits else ""))
    return "\n".join(lines) + (f"\n\n**Answer:** `{p.answer}`" if p.answer else "")


async def render_segment(ctx: JobContext, p: BoardPlan, work: Path,
                         voice_id: str | None = None) -> tuple[BoardPlan, dict]:  # fmt: skip
    """Typeset, voice and render a board plan to `work` (board.mp4 + per-step s{i}.mp3),
    without delivering anything: the clip is embedded as a scene in a bigger video.
    Returns the (possibly repaired) plan and its timeline."""
    work.mkdir(parents=True, exist_ok=True)
    if ctx.settings.board_runner == "local":
        p, tl, _chk, _res = await _board(ctx, Runner(local=work), p, p.problem, work, voice_id)
    else:
        mgr = ctx._manager.sandboxes
        cfg = dataclasses.replace(ctx.settings, sandbox_image="board",
                                  sandbox_cpu=ctx.settings.board_cpu, sandbox_memory_mb=8192,
                                  sandbox_max_seconds=2400)  # fmt: skip
        ctx.phase("sandbox", "Starting the board renderer")
        async with mgr.session(ctx.id, ctx.conversation_id, settings=cfg) as (sid, box):  # noqa: SIM117
            async with mgr.busy(sid):
                p, tl, _chk, _res = await _board(ctx, Runner(box=box), p, p.problem, work, voice_id)
    return p, tl
