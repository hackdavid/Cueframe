"""Plan a tutorial for any Linux/browser task, then rehearse it before recording.

plan: docs research (official docs, else model knowledge) -> Muse Spark writes a
`TutorialPlan` (steps, commands, checks, narration) -> static safety/shape checks.
rehearse: run every step once in an unrecorded sandbox and check each result; a
failing step is repaired by the model from its real output, then retried. Only a
plan whose every step passed is recorded, so the video never shows guesswork.
"""

import asyncio
import re
import shlex
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.contracts import ResearchRequest
from app.agents.docs_research import DocsResearchAgent
from app.heal import Healer, short
from app.jobs import JobContext
from app.sandbox.base import ANSI, Sandbox
from app.sandbox.contracts import Action, ActionRecord, live_path
from app.sandbox.manager import SandboxLimit
from app.tutorial.build import COLS, PROMPT

MAX_REPAIRS = 3
SANDBOX_TRIES = 2  # a sandbox that breaks mid-run is replaced this many times - 1
MIN_WORDS = 120  # ~2.5 words/s plus on-screen time: about a minute of video


class PlannedStep(BaseModel):
    id: str = Field(description="Short lowercase slug, unique, e.g. 'install'")
    label: str = Field(description="2-4 word step title shown on screen")
    kind: Literal["terminal", "open_url", "key", "click", "type", "scroll", "launch"]
    command: str = Field(
        description="terminal: ONE non-interactive bash line; launch: the desktop app's "
        "command, e.g. 'mousepad'; else ''"
    )
    app: str = Field(
        description="click/type/scroll/key: 'browser' or the desktop app the step acts in "
        "(its command name, e.g. 'mousepad'); launch: the same name; terminal/open_url: ''"
    )
    target: str = Field(
        description="click/scroll/type: the element's visible words exactly as on screen "
        "(button or link text, a field's label or placeholder, a menu name); type may use "
        "'' to type where the cursor already is; else ''"
    )
    text: str = Field(description="type: the text to type; else ''")
    enter: bool = Field(description="type: press Enter after typing")
    url: str = Field(description="open_url: the address to type in the browser; else ''")
    keys: str = Field(description="key: an xdotool key name such as 'F5'; else ''")
    installs: bool = Field(description="True if the step installs software")
    expect: str = Field(
        description="terminal: short text that must appear in the output when the step "
        "worked (e.g. '200 OK', a version number prefix); browser/desktop steps: short text "
        "that must be visible on the page or in the app afterwards; '' if nothing to check"
    )
    narration: str = Field(description="2 short spoken sentences while the step runs")
    after: str = Field(description="1 sentence spoken after a slow command ends; usually ''")
    badge: str = Field(description="2-4 words shown when the step is done")
    highlight: str = Field(description="1-4 word label on the typed command or URL")
    output_label: str = Field(description="terminal: 1-3 word label on the result; else ''")
    success: bool = Field(description="True for the payoff moments (plays a success chime)")


class SayAs(BaseModel):
    written: str = Field(description="Term as written, e.g. 'nginx'")
    spoken: str = Field(description="How the narration spells it for the voice: 'engine-x'")


class TutorialPlan(BaseModel):
    """LLM contract: a complete, runnable tutorial."""

    feasible: bool = Field(description="False if it can't be shown in this sandbox")
    reason: str = Field(description="Why not, when feasible is false; else ''")
    title: str = Field(description="YouTube-style title, max 60 characters")
    subtitle: str = Field(description="Short subtitle, e.g. 'in about a minute'")
    summary: str = Field(description="One sentence: what the viewer will do")
    software: str = Field(description="Main software shown, e.g. 'nginx'")
    tags: list[str] = Field(description="6-10 YouTube tags")
    done: str = Field(description="Outro headline, 2-5 words, e.g. 'Your site is live'")
    intro: str = Field(description="Spoken hook, 1-2 sentences")
    outro: str = Field(description="Spoken wrap-up, 1-2 sentences")
    say: list[SayAs] = Field(description="Terms the narration spells differently")
    steps: list[PlannedStep]


class StepFix(BaseModel):
    """LLM contract: a repaired step after a failed rehearsal."""

    fixable: bool = Field(description="False if the task can't work in this sandbox")
    reason: str = Field(description="What went wrong, one sentence")
    step: PlannedStep


SANDBOX = """The sandbox is a fresh Debian Linux desktop at 1920x1080 with
internet access. Commands run as root in a bash terminal (no sudo needed, no password,
no systemd: start daemons directly, e.g. `nginx`, not `systemctl`). GUI apps: this
terminal, a Chromium browser, and Debian desktop apps you install with apt-get and open
with a launch step (GTK apps work best, e.g. mousepad, gnome-calculator, thunar,
gnumeric, because their buttons and fields are found through the accessibility tree).
Every app fills the screen and only one is shown at a time. The working directory is
/work. Package manager: apt-get (index is already updated). Python 3 is installed."""

RULES = f"""You plan short, real, narrated video tutorials (about 60-90 seconds).
{SANDBOX}

Write 3-7 steps that a beginner can follow and that really work in that sandbox:
- terminal steps: ONE line each, fully non-interactive (`apt-get install -y ...`,
  `--yes`, no editors, no prompts, no `less`/pagers, nothing that runs forever in the
  foreground; start servers in the background or as daemons). Keep each step's output
  to at most 12 lines (`| head -n 12`, `-q`) so the result fits on screen, and keep
  commands short (under 70 characters when possible) so they read well zoomed in.
- browser steps: kind=open_url with a URL (localhost services or public sites);
  kind=click / type / scroll on a real element named by its visible words in `target`
  (e.g. click 'Download', type 'pandas' into 'Search'); kind=key (e.g. 'F5' to reload).
  Set app='browser'. The browser comes to the front automatically.
- desktop app steps: install it in a terminal step (installs=true), then kind=launch
  with command and app set to its command name (e.g. 'mousepad'), then click / type /
  key steps with that app. Name targets by the words visible on screen (menu names,
  button labels); a click or type step that names nothing visible fails.
- Give every click/type/launch step an `expect` text that is visible afterwards when
  it worked (a page heading, the typed text, a dialog title).
- Mark every software install with installs=true; the user is asked about it first.
- Give each terminal step a concrete `expect` text when the exit code alone doesn't
  prove it worked. End with a step that proves the result (e.g. curl, --version, ls).
- Never touch anything outside the sandbox, no destructive or system-wide changes
  beyond the task, no secrets or passwords, no sign-ups, logins or purchases.
Narration is read by a text-to-speech voice: friendly, confident, short sentences, no
markdown, and write names the way they're said (e.g. 'engine-x' for nginx, 'J Q' for
jq); list each respelling in `say`. Respell ONLY inside narration, intro and outro:
title, labels, badges and highlights are shown on screen, so write names normally there.
Never claim results the steps don't produce. If the task needs Windows/macOS, an app that
isn't packaged for Debian, hardware, paid accounts or credentials, set feasible=false
and explain."""

FIX = f"""A step of a tutorial plan failed when rehearsed for real. Rewrite just that
step so it works, keeping its id and purpose (you may change the command, url, expect
text, target and narration). Base the fix on the real output: when an element wasn't
found, the failure lists the elements that ARE visible; pick the right one from it.
{SANDBOX}"""

GUI = ("click", "type", "scroll", "key", "launch")
APP_CMD = re.compile(r"[a-z0-9][a-z0-9.+_-]*( [\w./=:-]+){0,3}")
BAD_URL = re.compile(
    r"^\s*(file|javascript|data|chrome|about|view-source):|169\.254\.|metadata\.google|"
    r"//[^/]*@",
    re.I,
)
UNSAFE_TARGET = re.compile(
    r"\b(pass(word|phrase)?|sign ?up|register|log ?in|sign ?in|buy|purchase|checkout|"
    r"pay(ment)?|place order|credit card|delete account)\b",
    re.I,
)
SECRETISH = re.compile(r"(sk|pk|ghp|xox[bp]|AKIA)[-_A-Za-z0-9]{12,}|[A-Za-z0-9+/]{40,}")

BLOCKED = re.compile(
    r"\b(shutdown|reboot|poweroff|halt|mkfs\S*|dd\s+if=|init\s+0)\b|rm\s+-\S*r\S*\s+/(\s|$)"
    r"|:\(\)\s*\{|\b(vim?|nano|emacs|less|more|top|htop|watch)\b(?!-)"
)


def problems(plan: TutorialPlan) -> list[str]:
    out = []
    if not 2 <= len(plan.steps) <= 8:
        out.append(f"use 3-7 steps, not {len(plan.steps)}")
    ids = [s.id for s in plan.steps]
    if len(set(ids)) != len(ids) or {"title", "outro", "intro"} & set(ids):
        out.append("step ids must be unique and not 'title', 'intro' or 'outro'")
    for s in plan.steps:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", s.id):
            out.append(f"step id {s.id!r} must be a short lowercase slug")
        if s.kind == "terminal":
            if not s.command.strip() or "\n" in s.command:
                out.append(f"step {s.id}: terminal steps need one command line")
            elif BLOCKED.search(s.command):
                out.append(f"step {s.id}: command is interactive or destructive")
        if s.kind == "open_url" and not s.url.strip():
            out.append(f"step {s.id}: open_url needs a url")
        if s.kind == "open_url" and BAD_URL.search(s.url):
            out.append(f"step {s.id}: only http(s) addresses without credentials are allowed")
        if s.kind == "key" and not s.keys.strip():
            out.append(f"step {s.id}: key needs keys")
        if s.kind in ("click", "scroll") and not s.target.strip():
            out.append(f"step {s.id}: {s.kind} needs the target's visible words")
        if s.kind == "type" and not s.text:
            out.append(f"step {s.id}: type needs text")
        if s.kind in ("click", "type") and UNSAFE_TARGET.search(s.target):
            out.append(f"step {s.id}: no logins, sign-ups, passwords or purchases")
        if s.kind == "type" and (SECRETISH.search(s.text) or "\n" in s.text):
            out.append(f"step {s.id}: type one line of plain text, never a secret")
        if s.kind == "launch":
            cmd = s.command.strip()
            if not APP_CMD.fullmatch(cmd) or BLOCKED.search(cmd):
                out.append(f"step {s.id}: launch needs a desktop app command like 'mousepad'")
        if not s.narration.strip():
            out.append(f"step {s.id}: narration is empty")
    spoken = " ".join([plan.intro, plan.outro, *(f"{s.narration} {s.after}" for s in plan.steps)])
    if len(plan.steps) >= 2 and len(spoken.split()) < MIN_WORDS:
        out.append(
            f"narration is {len(spoken.split())} words; write at least {MIN_WORDS} in total "
            "(intro, outro and 2 sentences per step) so the video runs about a minute"
        )
    return out


def action(s: PlannedStep) -> dict[str, Any]:
    if s.kind == "terminal":
        cmd = s.command.strip()
        if s.installs and cmd.startswith("apt") and "DEBIAN_FRONTEND" not in cmd:
            cmd = f"DEBIAN_FRONTEND=noninteractive {cmd}"
        return {"kind": "terminal", "command": cmd, "clear": True, "ask": s.installs,
                "timeout": 300 if s.installs else 120}  # fmt: skip
    if s.kind == "open_url":
        return {"kind": "open_url", "url": s.url.strip(), "app": "browser"}
    app = s.app.strip() or "browser"
    if s.kind == "launch":
        cmd = s.command.strip()
        return {"kind": "launch", "command": cmd, "app": s.app.strip() or cmd.split()[0]}
    if s.kind == "click":
        return {"kind": "click", "target": s.target.strip(), "app": app}
    if s.kind == "type":
        return {"kind": "type", "target": s.target.strip() or None, "text": s.text,
                "enter": s.enter, "app": app}  # fmt: skip
    if s.kind == "scroll":
        return {"kind": "scroll", "target": s.target.strip(), "app": app}
    return {"kind": "key", "keys": s.keys.strip(), "app": app}


def written(text: str, say: dict[str, str]) -> str:
    """Undo voice respellings in on-screen text ('Install J Q' -> 'Install jq')."""
    for spoken, name in sorted(say.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(spoken)}(?![\w-])", name, text, flags=re.I)
    return text.strip()


def zoom_for(command: str, best: float = 1.8) -> float:
    """Largest zoom (<= best) that keeps the whole typed command line in frame."""
    need = PROMPT + len(command) + 2
    return 1.0 if need > COLS else round(max(1.0, min(best, COLS / need)), 2)


def to_spec(plan: TutorialPlan, goal: str) -> dict[str, Any]:
    """The plan in the recipe format `capture`/`build`/`edit` already use."""
    say = {x.spoken.strip().lower(): x.written.strip() for x in plan.say if x.spoken and x.written}
    steps, lines = [], {"intro": plan.intro.strip(), "outro": plan.outro.strip()}
    for s in plan.steps:
        keys = [s.id]
        lines[s.id] = s.narration.strip()
        if s.after.strip() and s.kind == "terminal":
            keys.append(f"{s.id}_done")
            lines[f"{s.id}_done"] = s.after.strip()
        hl: dict[str, Any] = {"command": written(s.highlight, say) or None}
        step = {"id": s.id, "label": written(s.label, say), "narration": keys,
                "action": action(s), "badge": written(s.badge, say) or None,
                "success": s.success, "highlight": hl, "expect": s.expect.strip(),
                "output_label": written(s.output_label, say)}  # fmt: skip
        if s.kind == "terminal":
            step["zoom"] = zoom_for(step["action"]["command"])
            step["zoom_out_after"] = s.installs
        steps.append(step)
    return {
        "id": "planned",
        "goal": goal,
        "title": written(plan.title, say),
        "subtitle": written(plan.subtitle, say),
        "summary": plan.summary.strip(),
        "software": plan.software.strip(),
        "tags": plan.tags,
        "done": written(plan.done, say),
        "say": say,
        "steps": steps,
        "lines": lines,
    }


def describe_plan(spec: dict[str, Any]) -> str:
    out = []
    for i, s in enumerate(spec["steps"], 1):
        a = s["action"]
        on = f" in {a.get('app')}" if a.get("app") not in (None, "browser") else ""
        what = {"terminal": f"`{a.get('command')}`", "open_url": f"open `{a.get('url')}`",
                "key": f"press `{a.get('keys')}`{on}", "launch": f"open `{a.get('command')}`",
                "click": f"click “{a.get('target')}”{on}",
                "scroll": f"scroll to “{a.get('target')}”{on}",
                "type": f"type `{a.get('text')}`"
                + (f" into “{a['target']}”" if a.get("target") else "") + on,
                }[a["kind"]]  # fmt: skip
        out.append(f"{i}. **{s['label']}**: {what}")
    return "\n".join(out)


async def research(ctx: JobContext, goal: str) -> str:
    """Grounding from the official docs (or model knowledge); '' if research fails."""

    def progress(stage: str, label: str, value: float) -> None:
        ctx.phase("research", label)

    try:
        res = await DocsResearchAgent(
            ctx.llm, ctx.settings
        ).run(  # type: ignore[arg-type]
            ResearchRequest(query=goal[:2000], include_images=False), progress
        )
    except Exception as exc:  # research is best effort; the planner can still work
        ctx.thinking("Research", f"Docs research failed: {exc}")
        return ""
    src = ", ".join(s.url for s in res.sources[:4]) or "model knowledge"
    ctx.thinking("Research", f"{len(res.steps)} steps from {src} ({res.confidence:.0%})")
    steps = "\n".join(f"{s.index}. {s.title}: {s.detail}" for s in res.steps)
    return f"Reference steps ({res.source}, confidence {res.confidence:.2f}, {src}):\n{steps}"


async def plan(ctx: JobContext, goal: str, avoid: str = "") -> dict[str, Any] | str:
    """A validated spec, or a str explaining why the task can't be shown. `avoid` is
    a failure from a previous plan's rehearsal; the new plan must work around it."""
    if ctx.llm is None or not ctx.llm.configured:
        return "Planning a new tutorial needs MODEL_API_KEY on the server."
    ctx.phase("research", "Reading the official docs")
    ref = await research(ctx, goal)
    ctx.phase("plan", "Planning the tutorial steps")
    prompt = f"Task: {goal}\n\n{ref}".strip()
    if avoid:
        prompt += (f"\n\nA previous plan for this task failed when run for real: {avoid}. "
                   "Use a different approach that avoids that failure.")  # fmt: skip
    for _ in range(3):
        p, _res = await ctx.llm.parse(
            TutorialPlan, purpose="tutorial.plan", model=ctx.settings.research_model,
            instructions=RULES, input=prompt,
        )  # fmt: skip
        if not p.feasible:
            return p.reason or "That task can't be shown in a Linux terminal and browser."
        issues = problems(p)
        ctx.thinking("Plan", "; ".join(issues) or f"{len(p.steps)} steps: {p.title}")
        if not issues:
            return to_spec(p, goal)
        prompt += f"\n\nYour previous plan had problems, fix them: {'; '.join(issues)}"
    return f"I couldn't produce a safe, runnable plan ({'; '.join(issues)})."


def output_lines(rec: ActionRecord | dict[str, Any], command: str) -> list[str]:
    """What the command printed (without the echoed command and the next prompt)."""
    tail = rec["output_tail"] if isinstance(rec, dict) else rec.output_tail
    lines = [x.rstrip() for x in ANSI.sub("", tail).replace("\r", "").splitlines()]
    key = command.strip()[-30:]
    echoed = [i for i, x in enumerate(lines) if key and key in x and "@cueframe" in x]
    start = echoed[-1] + 1 if echoed else 0
    body = lines[start:]
    while body and (not body[-1].strip() or "@cueframe" in body[-1]):
        body.pop()
    return [x for x in body if "@cueframe" not in x][-8:]


async def check(box: Sandbox, step: dict[str, Any], rec: ActionRecord) -> ActionRecord:
    """Verify the step's result beyond the exit code (expected output, reachable URL)."""
    if not rec.ok:
        return rec
    a = step["action"]
    expect = (step.get("expect") or "").lower()
    if a["kind"] == "terminal" and expect and expect not in rec.output_tail.lower():
        full = (await box.exec("tail -c 20000 /tmp/cf/term.log | tr -d '\\r'")).stdout
        after = ANSI.sub("", full).rpartition(a["command"].strip()[-30:])[2]
        if expect not in after.lower():
            rec.ok, rec.error = False, f"Output did not contain {step['expect']!r}"
    if a["kind"] in GUI and expect:
        return await _visible(box, a.get("app") or "browser", step["expect"], rec)
    if a["kind"] == "open_url":
        url = a["url"] if "://" in a["url"] else f"http://{a['url']}"
        res = await box.exec(f"curl -s -o /dev/null -m 10 -w '%{{http_code}}' {url!r}", 20)
        code = res.stdout.strip()[-3:]
        if not code.isdigit() or not 200 <= int(code) < 400:
            rec.ok, rec.error = False, f"{url} answered HTTP {code or 'nothing'}"
    return rec


async def _visible(box: Sandbox, app: str, expect: str, rec: ActionRecord) -> ActionRecord:
    """Wait up to ~8 s for `expect` to show in the page text / app's accessibility tree."""
    text = ""
    for _ in range(8):
        res = await box.exec(f"/opt/cf/ui.py text {shlex.quote(app)} | head -c 60000", 20)
        text = res.stdout
        if res.returncode == 0 and expect.lower() in text.lower():
            return rec
        await asyncio.sleep(1)
    seen = " | ".join(x.strip() for x in text.splitlines() if x.strip())[:600]
    rec.ok, rec.error = False, f"{expect!r} is not visible in {app} afterwards. Visible: {seen}"
    return rec


def what(step: dict[str, Any]) -> str:
    a = step["action"]
    return (a.get("command") or a.get("url") or a.get("keys") or a.get("text")
            or a.get("target") or a["kind"])  # fmt: skip


def failure(step: dict[str, Any], rec: ActionRecord | dict[str, Any]) -> tuple[str, str]:
    """Why a step failed, and what it printed."""
    get = rec.get if isinstance(rec, dict) else lambda k: getattr(rec, k)
    out = "\n".join(output_lines(rec, step["action"].get("command") or ""))
    return get("error") or f"exit code {get('returncode')}", out


async def repair(
    ctx: JobContext, spec: dict[str, Any], i: int, rec: ActionRecord | dict[str, Any]
) -> str | None:
    """Rewrite failing step `i` in place from its real output. None when a fix was made,
    else why the step can't work here. Narration lines of the step are regenerated."""
    step = spec["steps"][i]
    why, out = failure(step, rec)  # GUI failures carry the visible elements in `why`
    if ctx.llm is None or not ctx.llm.configured:
        return f"step **{step['label']}** failed ({why}) and no model is configured to fix it"
    goal = spec.get("goal") or spec["title"]
    fix, _ = await ctx.llm.parse(
        StepFix, purpose="tutorial.repair", model=ctx.settings.research_model,
        instructions=FIX,
        input=f"Task: {goal}\nPlan:\n{describe_plan(spec)}\n\nFailed step "
        f"{i + 1} ({step['id']}): {step}\nFailure: {why}\nOutput:\n{out[-1500:]}",
    )  # fmt: skip
    fix.step.id = step["id"]
    if not fix.fixable or (bad := problems_one(fix.step)):
        return f"step **{step['label']}** can't work here: {fix.reason or '; '.join(bad)}"
    new = to_spec(_stub([fix.step]), goal)
    spec["steps"][i] = new["steps"][0]
    for k in [k for k in spec["lines"] if k in (step["id"], f"{step['id']}_done")]:
        del spec["lines"][k]
    spec["lines"].update({k: v for k, v in new["lines"].items()
                          if k not in ("intro", "outro")})  # fmt: skip
    return None


async def rehearse(ctx: JobContext, spec: dict[str, Any], heal: Healer | None = None) -> str | None:
    """Run the plan once, unrecorded; repair failing steps in place. Returns None when
    every step passed, else why it can't be done. Updates `spec` with the fixes. A
    sandbox that breaks (not a step that fails) is replaced and the rehearsal restarts."""
    heal = heal or Healer(ctx)
    state = {"repairs": 0}
    for attempt in range(1, SANDBOX_TRIES + 1):
        try:
            return await _rehearse(ctx, spec, heal, state)
        except Exception as exc:
            if attempt == SANDBOX_TRIES or isinstance(exc, SandboxLimit):
                raise
            heal(f"The rehearsal sandbox broke ({short(exc)}); starting a fresh one")
    raise AssertionError("unreachable")


async def _rehearse(
    ctx: JobContext, spec: dict[str, Any], heal: Healer, state: dict[str, int]
) -> str | None:
    mgr = ctx._manager.sandboxes
    ctx.phase("rehearse", "Rehearsing every step in a private sandbox (not recorded)")
    async with mgr.session(ctx.id, ctx.conversation_id) as (sid, box):
        ctx.emit("sandbox.started", {"sandbox_id": sid, "live_url": live_path(ctx.id)})
        i = 0
        while i < len(spec["steps"]):
            step = spec["steps"][i]
            ctx.phase("rehearse", f"Rehearsing step {i + 1}/{len(spec['steps'])}: {step['label']}")
            mgr.touch(sid)
            async with mgr.busy(sid):
                rec = await check(
                    box, step, await box.run(i, Action.model_validate(step["action"]))
                )
            mgr.touch(sid)
            if rec.ok:
                i += 1
                continue
            why, out = failure(step, rec)  # GUI failures carry the visible elements in `why`
            ctx.thinking("Rehearsal", f"Step {step['id']} failed: {why}\n{out[-600:]}")
            if state["repairs"] >= MAX_REPAIRS:
                await mgr.stop(sid, "rehearsal failed")
                return (
                    f"step **{step['label']}** still fails after {state['repairs']} fixes ({why})"
                )
            state["repairs"] += 1
            ctx.phase("plan", f"Fixing step {i + 1} from its real output")
            if err := await repair(ctx, spec, i, rec):
                await mgr.stop(sid, "rehearsal failed")
                return err
            heal(f"Rehearsal: step **{step['label']}** failed ({why}); rewrote it from the "
                 f"real output as `{what(spec['steps'][i])}`")  # fmt: skip
        await mgr.stop(sid, "rehearsal passed")
    spec["rehearsal"] = {"repairs": state["repairs"], "sandbox_id": sid}
    return None


def _stub(steps: list[PlannedStep]) -> TutorialPlan:
    blank = dict.fromkeys(("reason", "title", "subtitle", "summary", "software", "done"), "")
    return TutorialPlan(feasible=True, tags=[], intro="", outro="", say=[], steps=steps, **blank)


def problems_one(step: PlannedStep) -> list[str]:
    return [p for p in problems(_stub([step, step])) if "unique" not in p and "words" not in p]
