"""Math tutor jobs: chat -> board plan -> SymPy checks -> voice -> board render -> R2.
The board is drawn, never recorded: it renders in a headless sandbox (or on this server
with CUEFRAME_BOARD_RUNNER=local) and the narration is mixed in here."""

import asyncio
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.board import timeline
from app.board.contracts import BoardPlan, Level, normalize, problems
from app.jobs import JobContext, JobFailed
from app.tutorial import formats
from app.tutorial.voice import speak

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


async def clarify(ctx: JobContext, level: Level | None) -> Level:
    if level:
        return level
    ans = await ctx.ask("Who is this lesson for?", list(LEVELS), key="level")
    return LEVELS.get(ans, "school")


async def _sympy(plan: BoardPlan, timeout: float = 240) -> tuple[int, list[str]]:
    """Run the checks in a child process (each is bounded; the whole run is killed if
    it still overruns)."""
    p = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.board.checks", stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    try:
        out, err = await asyncio.wait_for(p.communicate(plan.model_dump_json().encode()), timeout)
    except TimeoutError:
        p.kill()
        await p.wait()
        return sum(len(s.checks) for s in plan.steps), [f"the checks took over {timeout:.0f} s"]
    if p.returncode:
        raise RuntimeError(f"math checks crashed: {err.decode()[-500:]}")
    res = json.loads(out)
    return res["checks"], res["failed"]


async def _checked(plan: BoardPlan) -> tuple[int, list[str]]:
    bad = problems(plan)
    n, failed = await _sympy(plan)
    if not n:
        bad.append("there are no checks: every step that changes an equation or computes a "
                   "value needs one")  # fmt: skip
    return n, bad + failed


async def plan(ctx: JobContext, problem: str, level: Level, seconds: int | None) -> BoardPlan:
    want = (f"Aim for about {seconds} s of narration in total." if seconds
            else "Take as long as the problem needs; don't pad.")  # fmt: skip
    base = f"Level: {level}\n{want}\nProblem from the student:\n{problem}"
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
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.board.engine", cmd, str(self.local), *args,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )  # fmt: skip
            out = (await asyncio.wait_for(p.communicate(), timeout))[0].decode()
        lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
        if not lines:
            raise RuntimeError(f"board engine gave no result: {out[-500:]}")
        return json.loads(lines[-1])

    async def get(self, name: str) -> bytes:
        if self.box is not None:
            return await self.box.read_file(f"{REMOTE}/{name}")
        return (self.local / name).read_bytes()  # type: ignore[operator]


async def _voice(p: BoardPlan, work: Path) -> list[tuple[float, list[dict]]]:
    sem = asyncio.Semaphore(4)

    async def one(i: int, say: str) -> tuple[float, list[dict]]:
        async with sem:
            audio, words = await speak(say)
        (work / f"s{i}.mp3").write_bytes(audio)
        ws = [w.model_dump() for w in words]
        return (ws[-1]["end"] + 0.1 if ws else 1.0), ws

    return list(await asyncio.gather(*(one(i, s.say) for i, s in enumerate(p.steps))))


async def _board(ctx: JobContext, run: Runner, p: BoardPlan, problem: str, work: Path,
                 ) -> tuple[BoardPlan, dict, dict, dict]:  # fmt: skip
    """LaTeX check (repairing the plan from the errors), voice, then the render."""
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
            instructions=skill(),
            input=f"Plan:\n{p.model_dump_json()}\n\nThese ops failed to render; return the "
            "whole plan with only those ops fixed:\n- " + "\n- ".join(errs),
        )  # fmt: skip
        fixed.level = p.level
        keep_checks(p, fixed)
        n, issues = await _checked(normalize(fixed))
        if issues:
            raise JobFailed(f"The fixed plan failed its checks: {'; '.join(issues)}",
                            {"issues": issues})  # fmt: skip
        p = fixed
    ctx.phase("voice", "Recording the teacher's voice")
    voiced = await _voice(p, work)
    tl = timeline.build(p, voiced)
    (work / "timeline.json").write_text(json.dumps(tl))
    await run.put("timeline.json", json.dumps(tl).encode())
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
    level = await clarify(ctx, level)
    ctx.phase("plan", "Planning the board")
    p = await plan(ctx, problem, level, seconds)
    ctx.reply(f"**{p.title}**: here's the board plan ({level}).\n\n" + describe(p))
    row = ctx.store.create_tutorial(conv["user_id"], conv["id"], ctx.id, "board", p.title,
                                    {"board": p.model_dump(), "problem": problem})  # fmt: skip
    tid = row["id"]
    ctx.store.update_tutorial(tid, active_job=ctx.id, status="rendering")
    work = ctx.settings.data_dir / "board" / tid
    work.mkdir(parents=True, exist_ok=True)
    if ctx.settings.board_runner == "local":
        p, tl, chk, res = await _board(ctx, Runner(local=work), p, problem, work)
    else:
        mgr = ctx._manager.sandboxes
        cfg = dataclasses.replace(ctx.settings, sandbox_image="board",
                                  sandbox_cpu=ctx.settings.board_cpu, sandbox_memory_mb=8192,
                                  sandbox_max_seconds=2400)  # fmt: skip
        ctx.phase("sandbox", "Starting the board renderer")
        async with mgr.session(ctx.id, ctx.conversation_id, settings=cfg) as (sid, box):  # noqa: SIM117
            async with mgr.busy(sid):
                p, tl, chk, res = await _board(ctx, Runner(box=box), p, problem, work)
    ctx.phase("mix", "Adding the voice and captions")
    video = await asyncio.to_thread(_mix, work, tl, len(p.steps))
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
    output = {**up, "stats": stats, "job_id": ctx.id}
    ctx.store.update_tutorial(tid, status="ready", version=1, output=output,
                              spec={"board": p.model_dump(), "problem": problem})  # fmt: skip
    ctx.emit("tutorial.ready", {"tutorial_id": tid, "version": 1, "video_url": up["video"]["url"]})
    ctx.reply(
        f"**Your lesson is ready: {p.title}** ({tl['seconds']:.0f} s, 1080p)\n\n"
        f'<video src="{up["video"]["url"]}" poster="{up["thumbnail"]["url"]}" controls></video>'
        f"\n\n[Captions (.srt)]({up['captions']['url']}) · [Thumbnail]({up['thumbnail']['url']})"
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
    return "\n".join(lines) + f"\n\n**Answer:** `{p.answer}`"
