"""Plan a tutorial for any Linux/browser task, then rehearse it before recording.

plan: docs research (official docs, else model knowledge) -> Muse Spark writes a
`TutorialPlan` (steps, commands, checks, narration) -> static safety/shape checks.
rehearse: run every step once in an unrecorded sandbox and check each result; a
failing step is repaired by the model from its real output, then retried. Only a
plan whose every step passed is recorded, so the video never shows guesswork.
"""

import asyncio
import base64
import re
import shlex
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.contracts import ResearchRequest
from app.agents.docs_research import DocsResearchAgent
from app.agents.trending import TrendBrief, TrendingAgent
from app.heal import Healer, short
from app.jobs import JobContext, JobFailed
from app.sandbox.base import ANSI, FOCUS, Sandbox, menu_path
from app.sandbox.contracts import Action, ActionRecord, live_path
from app.sandbox.manager import SandboxLimit
from app.tutorial import formats
from app.tutorial.build import COLS, PROMPT

MAX_REPAIRS = 3  # per step
MAX_TOTAL_REPAIRS = 10  # per rehearsal
MAX_REVIEWS = 2  # result reviews that may add steps, per rehearsal
REVIEW_TIMEOUT = 150
REPAIR_TIMEOUT = 90  # s per model call; a stalled call is retried sooner
SANDBOX_TRIES = 2  # a sandbox that breaks mid-run is replaced this many times - 1
MIN_WORDS = 120  # ~2.5 words/s plus on-screen time: about a minute of video
REEL_WORDS = (80, 135)  # influencer mode: the whole video stays under a minute
Mode = Literal["tutorial", "influencer", "explainer"]
CODE_COLS = 72  # longest written code line: 60 asked for, some slack


class PlannedStep(BaseModel):
    id: str = Field(description="Short lowercase slug, unique, e.g. 'install'")
    label: str = Field(description="2-4 word step title shown on screen")
    kind: Literal[
        "terminal", "open_url", "key", "click", "type", "scroll", "launch", "drag", "menu"
    ]
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
        "'' to type where the cursor already is; menu: the item path, e.g. 'Layer > Layer "
        "Style > Drop Shadow'; click/drag on the image or drawing area: 'canvas'; else ''"
    )
    at: list[float] = Field(
        description="click/drag on 'canvas' (or inside a big element): [x, y] fractions 0-1 "
        "of its box where the mouse presses, e.g. [0.5, 0.2]; else []"
    )
    to: list[float] = Field(description="drag: [x, y] fractions 0-1 where it releases; else []")
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
    pace: Literal["real", "fast", "cut"] = Field(
        "real",
        description="How the edit shows this step: 'real' time for the moment that matters; "
        "'fast' (sped up 2-8x) for typing, scrolling, installs and other waits; 'cut' to "
        "jump straight past a page load or long wait to its result",
    )


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
    drop: bool = Field(
        description="True only if the step isn't needed at all: what it waits for never "
        "comes because an earlier step already did it (e.g. a confirm button that doesn't "
        "exist because the change applied at once)"
    )
    before: list[PlannedStep] = Field(
        description="Steps the plan is missing that must run first, at most 2 (e.g. click a "
        "start screen's button, open the menu or dialog the step uses); [] if none"
    )
    step: PlannedStep


class Outcome(BaseModel):
    """LLM contract: does the end of a passed rehearsal show what the user asked for?"""

    done: bool = Field(description="True only if the images clearly show every result asked for")
    missing: list[str] = Field(
        description="Each asked-for result the images don't clearly show, e.g. 'the title "
        "is too small to read', 'the Drop Shadow dialog is still open'; [] if done"
    )
    after: str = Field(description="id of the plan step the new steps go right after")
    steps: list[PlannedStep] = Field(
        description="Steps that make the missing results happen, at most 4; [] if done"
    )


SANDBOX = """The sandbox is a fresh Debian Linux desktop at 1920x1080 with
internet access. Commands run as root in a bash terminal (no sudo needed, no password,
no systemd: start daemons directly, e.g. `nginx`, not `systemctl`). GUI apps: this
terminal, a Chromium browser, and Debian desktop apps you install with apt-get and open
with a launch step (GTK3 apps work best, e.g. mousepad, galculator, thunar,
gnumeric, because their buttons and fields are found through the accessibility tree).
Every app fills the screen and only one is shown at a time. The working directory is
/work. Package manager: apt-get (index is already updated). `python3` is Python 3.12
with pip: install Python libraries with `python3 -m pip install --quiet <names>`
(installs=true; not apt, whose Python packages belong to another interpreter). To show a
short program, write it with ONE `printf '%s\n' 'line 1' 'line 2' > /work/name.py` command
(each code line one quoted argument, no tabs, at most 60 characters so it reads well
zoomed in), then `cat -n` it and run it with `python3`; keep it under 12 lines. A program
that trains or fits a model must report its score on examples held out from training (a
few labelled test examples it never saw), never only on the training data, and the
narration must say which data the score is on.
Files the user attached to the request are already in /work under their own names.
Browser downloads are saved to /root/Downloads without asking. A browser file-open dialog
(GTK) accepts a typed full path: a type step with target '' and enter=true, e.g. text
'/work/photo.jpg'. Web editors such as Photopea (https://www.photopea.com, free, no
account) are driven through their menus (kind=menu, e.g. 'File > Open...', 'Select > Magic
Cut...', 'Layer > New Fill Layer > Gradient Fill', 'Layer > Arrange > Send Backward',
'Layer > Layer Style > Drop Shadow', 'File > Export as > PNG'), their tool buttons by full
name (e.g. 'Type Tool (T)'), dialog buttons ('OK', 'Save'), fields by label (type into
'Angle:', 'W:', 'X:', 'Y:'; typing replaces the value) and the drawing area as target
'canvas' with `at`/`to` points. Photopea without an account: 'Select > Remove BG' and
'Select > Subject' are Premium (an ad prompt blocks the page), never plan them. Cut a
person out with 'Select > Magic Cut...': drag green strokes on 'canvas' down the body and
across the shoulders (the image edges count as background), then 'OK' puts the cut-out on
a new layer. A new fill or text layer goes above the selected layer: use 'Layer > Arrange'
to order layers. The 'Properties' button opens/closes a panel over the image: close it
after changing values there. Size and place a text layer with the Move Tool then 'W:',
'X:', 'Y:' in Properties (image pixels). History lists each edit ('Refine Edge', 'New
Fill Layer', 'Type Tool', 'Layer Order', 'Layer Style'); Layers lists layer names, so use
those as `expect` (e.g. 'Gradient Fill 1', the typed title)."""

RULES = f"""You plan short, real, narrated video tutorials (about 60-90 seconds).
{SANDBOX}

Write 3-7 steps that a beginner can follow and that really work in that sandbox; a
multi-stage edit in a full app (photo editing, documents) may use up to 16 steps, one
visible action each, for a 2-4 minute video:
- terminal steps: ONE line each, fully non-interactive (`apt-get install -y ...`,
  `--yes`, no editors, no prompts, no `less`/pagers, nothing that runs forever in the
  foreground; start servers in the background or as daemons; never drive the GUI from
  the terminal with xdotool/wmctrl, use click/type/key steps for that). Keep each step's output
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
- menus: one kind=menu step per command, the full path from the menu bar to the item
  as shown on screen. Dragging (a gradient, a selection, moving a layer): kind=drag on
  'canvas' from `at` to `to`. Clicking a spot on the image: kind=click, target 'canvas',
  with `at`.
- To open a file in a web app, use its own open command (e.g. menu 'File > Open...')
  and type the full /work path into the file dialog. Prove an exported or saved file
  with a final terminal step (e.g. `file /root/Downloads/*.png`, expect 'PNG image').
- Give every click/type/launch/menu/drag step an `expect` text that is visible afterwards when
  it worked (a page heading, the typed text, a dialog title). Text and shapes drawn inside
  a canvas (drawing apps, maps, image editors) are pixels, not page text: for those steps
  expect a label the page itself shows afterwards, or leave expect empty and the finished
  picture is checked by eye.
- A dialog a step opens (e.g. 'Layer > Layer Style > Drop Shadow') stays open until its
  'OK' is clicked: plan that click before the next menu. A title must be readable at a
  glance: size and place it (e.g. 'W:' about half the image width) clear of the subject.
- Pages are waited on until they finish loading, and cookie or consent banners are
  closed automatically with their least-sharing choice (Reject all, Dismiss): never plan
  steps for cookie, consent or privacy banners.
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

INFLUENCER = f"""{RULES}

STYLE: influencer explainer, not a lesson. Same real steps and checks, but the video is
made to be watched to the end on social media, and the WHOLE video stays under 60 seconds:
- intro: a one-sentence scroll-stopping hook with a concrete, surprising fact or number
  from the brief (e.g. 'Two thousand developers starred this in one day. Here's why.').
- 4-6 browser steps that SHOW the thing: open its page, scroll to the headings or words
  that prove each point (use the brief's visible headings as scroll targets, exactly as
  written), open its paper, code or demo page. No installs or terminal steps unless the
  request asks to run it.
- narration: ONE punchy, conversational sentence per step (8-16 words), speaking to the
  viewer ('you'), with energy and no filler; leave `after` empty. Total spoken words
  (intro + steps + outro) {REEL_WORDS[0]}-{REEL_WORDS[1]}.
- outro: the takeaway in one sentence plus a call to action ('Star it and follow for more').
- title: a hook of max 50 characters; subtitle: where and when it is trending, e.g.
  'Trending on GitHub today'; badges are short reactions ('Wow, 98% accuracy').
- Use ONLY facts and numbers from the brief or the page itself, never from memory, and
  say numbers the way the brief gives them with their date or source. No rank ('number
  one'), price ('free'), comparison or superlative unless the brief states it.
- software: the site shown, e.g. 'GitHub' or 'Hugging Face'."""

EXPLAINER = """

STYLE OVERRIDE: a general explainer, not a reel. Calm, clear and curious instead of hype:
- intro: the question or problem the video answers, in one sentence.
- steps follow problem, idea, how it works, why it matters: 1-2 sentences each
  (10-28 words per step), each said while its proof is on screen.
- outro: the one-line takeaway; a call to action is optional.
- title: a clear question or statement, max 50 characters."""


def words(mode: Mode, seconds: int | None = None) -> tuple[int, int]:
    """Total spoken words (min, max; max 0 = none) for `mode` at `seconds` of video."""
    if not seconds:
        return REEL_WORDS if mode != "tutorial" else (MIN_WORDS, 0)
    if mode == "tutorial":  # steps run on screen between lines: fewer words per second
        return round(seconds * 1.0), round(seconds * 3.2)
    return round(seconds * 1.35), round(seconds * 2.25)


def instructions(mode: Mode, seconds: int | None = None, skill: str = "") -> str:
    """The planner's system prompt for `mode`, sized to `seconds`, with the format skill."""
    lo, hi = words(mode, seconds)
    if mode == "tutorial":
        text = RULES
        if seconds:
            text += f"\n\nLENGTH: about {seconds} seconds of video; total spoken words {lo}-{hi}."
    else:
        text = INFLUENCER.replace("under 60 seconds", f"about {seconds or 60} seconds")
        text = text.replace(f"{REEL_WORDS[0]}-{REEL_WORDS[1]}", f"{lo}-{hi}")
        if mode == "explainer":
            text += EXPLAINER
    if skill:
        text += f"\n\nCONTENT SKILL (how this kind of video is delivered):\n{skill}"
    return text


GROUNDED = """You fact-check the narration of a short video against its source brief. List
each spoken claim that the brief does not support: a number, rank, date, price, feature or
comparison that isn't in the brief, or that the brief contradicts. Opinions, calls to
action and plain descriptions of what is on screen ('here is the README') are fine. Quote
each unsupported claim briefly with why. Return an empty list when everything is supported."""


class Claims(BaseModel):
    """LLM contract: narration claims the source doesn't back up."""

    unsupported: list[str] = Field(description="Each unsupported claim and why; [] if none")


async def ungrounded(ctx: JobContext, plan: TutorialPlan, brief: str) -> list[str]:
    """Narration claims in `plan` that the trending `brief` doesn't support."""
    said = "\n".join([f"intro: {plan.intro}", *(f"{s.id}: {s.narration}" for s in plan.steps),
                      f"outro: {plan.outro}", f"title: {plan.title}"])  # fmt: skip
    try:
        c, _res = await ctx.llm.parse(
            Claims, purpose="tutorial.grounded", model=ctx.settings.research_model,
            instructions=GROUNDED, input=f"BRIEF:\n{brief}\n\nNARRATION:\n{said}",
            reasoning_effort="low", timeout=REPAIR_TIMEOUT,
        )  # fmt: skip
    except Exception as exc:  # the check is a safeguard; a failed call doesn't block the plan
        ctx.thinking("Fact check", f"skipped ({short(str(exc))})")
        return []
    return [f"narration not backed by the source: {x}" for x in c.unsupported]


FIX = f"""A step of a tutorial plan failed when rehearsed for real. Rewrite just that
step so it works, keeping its id and purpose (you may change the command, url, expect
text, target and narration). Base the fix on the real output: when an element wasn't
found, the failure lists the elements that ARE visible; pick the right one from it.
If the step can't work because something the plan never did has to happen first (a
start screen to dismiss, a dialog or menu to open), keep the step as it is and put
those missing steps in `before`; never replace a step with one that drops its purpose.
If the action ran without an error and the visible output shows it worked, but the
`expect` words never appear (a guessed heading, or text drawn inside a canvas), keep the
action and only change `expect` to words from the visible output, or empty it for a canvas.
If the step is not needed at all because an earlier step already did what it is for
(e.g. it clicks a confirm button that doesn't exist since the change applied at once),
set `drop` instead. When the failure says the element is covered by something (a dialog,
a panel, a pop-up), close that first with one of its own buttons (Close, Cancel, ×) in
`before`, unless the step needs what that dialog offers. Cookie and consent banners are
closed automatically: never add steps for them.
{SANDBOX}"""

REVIEW = f"""A tutorial plan was just rehearsed for real and every step passed its own
check. Look at the final screen and the exported file (if there is one) and judge whether
they clearly show every result the task asks for, as a viewer of the video would see it:
a title must be readable at a glance and placed where it doesn't hide the subject, an
effect must be visible, and no dialog may still be open over the work. If something is
missing, plan the steps that make it happen and say which step they go right after (before
the export or save steps, so the exported file has the change). Use only elements that
exist on that screen or that a menu opens, and never redo work that is already done.
{SANDBOX}"""

CONFIRM = re.compile(r"\s*(ok|apply|done|confirm|create|close)\s*$", re.I)
GUI = ("click", "type", "scroll", "key", "launch", "drag", "menu")
MAX_STEPS = 16
APP_CMD = re.compile(r"[a-z0-9][a-z0-9.+_-]*( [\w./=:-]+){0,3}")
BAD_URL = re.compile(
    r"^\s*(file|javascript|data|chrome|about|view-source):|169\.254\.|metadata\.google|"
    r"//[^/]*@",
    re.I,
)
CONSENT = re.compile(
    r"\b(cookies?|consent|accept all|reject all|do not sell|don'?t sell|personal info|gdpr)\b",
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
    r"|\b(xdotool|wmctrl|xte)\b"  # the GUI is driven by click/type/key steps, on screen
)


FIRST_PERSON = re.compile(
    r"^\W*(i\s+will|i'll|i\s+am\s+going|i'm\s+going|let\s+me|i\s+need\s+to)\b", re.I
)
FIRST_PERSON_ISSUE = "narration speaks as the operator"


def viewer_voice(line: str) -> str:
    """'I will open the menu.' -> 'Open the menu.': a last resort when a rewrite still
    speaks as the operator."""
    if not FIRST_PERSON.search(line):
        return line
    rest = FIRST_PERSON.sub("", line).lstrip(" ,:-")
    return rest[:1].upper() + rest[1:] if rest else line


def problems(plan: TutorialPlan, mode: Mode = "tutorial", seconds: int | None = None) -> list[str]:
    out = []
    if not 2 <= len(plan.steps) <= MAX_STEPS:
        out.append(f"use 3-{MAX_STEPS} steps, not {len(plan.steps)}")
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
            elif s.command.lstrip().startswith("printf") and any(
                len(x) > CODE_COLS for x in re.findall(r"'([^']*)'", s.command)[1:]
            ):
                out.append(f"step {s.id}: keep each code line at most {CODE_COLS} characters "
                           "so it isn't cut off zoomed in")  # fmt: skip
        if s.kind == "open_url" and not s.url.strip():
            out.append(f"step {s.id}: open_url needs a url")
        if s.kind == "open_url" and BAD_URL.search(s.url):
            out.append(f"step {s.id}: only http(s) addresses without credentials are allowed")
        if s.kind == "key" and not s.keys.strip():
            out.append(f"step {s.id}: key needs keys")
        if s.kind == "menu" and len(menu_path(s.target)) < 2:
            out.append(f"step {s.id}: menu needs a path like 'File > Open...'")
        if s.kind == "drag" and (not s.target.strip() or len(s.at) != 2 or len(s.to) != 2):
            out.append(f"step {s.id}: drag needs a target ('canvas') and [x, y] at and to")
        for name, pt in (("at", s.at), ("to", s.to)):
            if pt and (len(pt) != 2 or not all(0 <= v <= 1 for v in pt)):
                out.append(f"step {s.id}: {name} must be [x, y] fractions between 0 and 1")
        if s.kind in ("click", "scroll") and not s.target.strip():
            out.append(f"step {s.id}: {s.kind} needs the target's visible words")
        if s.kind == "type" and not s.text:
            out.append(f"step {s.id}: type needs text")
        if s.kind == "click" and CONSENT.search(s.target):
            out.append(f"step {s.id}: cookie/consent banners are closed automatically; "
                       "drop that step")  # fmt: skip
        if s.kind in ("click", "type", "menu", "drag") and UNSAFE_TARGET.search(s.target):
            out.append(f"step {s.id}: no logins, sign-ups, passwords or purchases")
        if s.kind == "type" and (SECRETISH.search(s.text) or "\n" in s.text):
            out.append(f"step {s.id}: type one line of plain text, never a secret")
        if s.kind == "launch":
            cmd = s.command.strip()
            if not APP_CMD.fullmatch(cmd) or BLOCKED.search(cmd):
                out.append(f"step {s.id}: launch needs a desktop app command like 'mousepad'")
        if not s.narration.strip():
            out.append(f"step {s.id}: narration is empty")
        elif FIRST_PERSON.search(s.narration):
            out.append(f"step {s.id}: {FIRST_PERSON_ISSUE} ({s.narration.split('.')[0]!r}); "
                       "talk to the viewer about what the screen shows")  # fmt: skip
    spoken = " ".join([plan.intro, plan.outro, *(f"{s.narration} {s.after}" for s in plan.steps)])
    n = len(spoken.split())
    lo, hi = words(mode, seconds)
    if mode != "tutorial":
        if not lo <= n <= hi:
            out.append(f"narration is {n} words; {mode} videos need {lo}-{hi} in total so "
                       f"the video runs about {seconds or 60} seconds")  # fmt: skip
    elif len(plan.steps) >= 2 and n < lo:
        out.append(
            f"narration is {n} words; write at least {lo} in total (intro, outro and "
            f"2 sentences per step) so the video runs about {seconds or 60} seconds"
        )
    elif hi and n > hi:
        out.append(f"narration is {n} words; keep it under {hi} so the video runs about "
                   f"{seconds} seconds")  # fmt: skip
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
        out = {"kind": "click", "target": s.target.strip(), "app": app}
        return {**out, "at": s.at} if len(s.at) == 2 else out
    if s.kind == "menu":
        return {"kind": "menu", "target": " > ".join(menu_path(s.target)), "app": app}
    if s.kind == "drag":
        return {"kind": "drag", "target": s.target.strip(), "at": s.at, "to": s.to, "app": app}
    if s.kind == "type":
        target = s.target.strip() or None  # typing into a named field replaces its text
        return {"kind": "type", "target": target, "text": s.text, "enter": s.enter,
                "clear": bool(target), "app": app}  # fmt: skip
    if s.kind == "scroll":
        return {"kind": "scroll", "target": s.target.strip(), "app": app}
    return {"kind": "key", "keys": s.keys.strip(), "app": app}


def written(text: str, say: dict[str, str]) -> str:
    """Undo voice respellings in on-screen text ('Install J Q' -> 'Install jq')."""
    for spoken, name in sorted(say.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(spoken)}(?![\w-])", name, text, flags=re.I)
    return text.strip()


def zoom_fit(chars: int, best: float = 1.8) -> float:
    """Largest zoom (<= best) at which a `chars`-wide output line stays in frame."""
    need = chars + 2
    return 1.0 if need > COLS else round(max(1.0, min(best, COLS / need)), 2)


def zoom_for(command: str, best: float = 1.8) -> float:
    """Largest zoom (<= best) that keeps the whole typed command line in frame."""
    need = PROMPT + len(command) + 2
    return 1.0 if need > COLS else round(max(1.0, min(best, COLS / need)), 2)


def to_spec(plan: TutorialPlan, goal: str, mode: Mode = "tutorial") -> dict[str, Any]:
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
        if s.pace != "real":
            step["pace"] = s.pace
        if s.kind == "terminal":
            step["zoom"] = zoom_for(step["action"]["command"])
            step["zoom_out_after"] = s.installs
        steps.append(step)
    return {
        "id": "planned",
        "goal": goal,
        "mode": mode,
        "kicker": "TRENDING · EXPLAINED IN 60 SECONDS" if mode == "influencer" else "",
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
                "double_click": f"double-click “{a.get('target')}”{on}",
                "menu": f"choose **{a.get('target')}**{on}",
                "drag": f"drag across “{a.get('target')}”{on}",
                "wait": f"wait {a.get('seconds')} s",
                "scroll": f"scroll to “{a.get('target')}”{on}",
                "type": f"type `{a.get('text')}`"
                + (f" into “{a['target']}”" if a.get("target") else "") + on,
                }.get(a["kind"], a["kind"])  # fmt: skip
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


async def scout(ctx: JobContext, goal: str) -> TrendBrief | str:
    """Find today's trending item for `goal` with the trending agent's source tools."""
    if ctx.llm is None or not ctx.llm.configured:
        return "Finding trending items needs MODEL_API_KEY on the server."
    ctx.phase("research", "Finding what's trending today")
    agent = TrendingAgent(ctx.llm, ctx.settings.research_model)
    try:
        brief, trace = await agent.run(goal[:2000], lambda m: ctx.thinking("Trending", m))
    except Exception as exc:
        return f"the trending sources couldn't be read ({short(str(exc))})"
    if not brief.found or not brief.url.startswith("https://"):
        return brief.reason or "no trending item fit the request"
    ctx.thinking("Trending", f"Picked {brief.name} ({brief.source}: {brief.why_trending}) "
                 f"after {len(trace)} tool calls")  # fmt: skip
    return brief


async def plan(
    ctx: JobContext,
    goal: str,
    avoid: str = "",
    mode: Mode = "tutorial",
    brief: str = "",
    seconds: int | None = None,
    board: dict[str, Any] | None = None,
) -> dict[str, Any] | str:
    """A validated spec, or a str explaining why the task can't be shown. `avoid` is
    a failure from a previous plan's rehearsal; the new plan must work around it. A
    trending `brief` replaces docs research; `mode` picks the style. With a target length
    in `seconds` the research is first storyboarded with the format's skill (`board`
    reuses an earlier storyboard)."""
    if ctx.llm is None or not ctx.llm.configured:
        return "Planning a new tutorial needs MODEL_API_KEY on the server."
    if brief:
        ref = brief
    else:
        ctx.phase("research", "Reading the official docs")
        ref = await research(ctx, goal)
    sb = None
    if seconds:
        sb = formats.Storyboard.model_validate(board) if board else None
        if sb is None and (sb := await formats.storyboard(ctx, goal, ref, mode, seconds)):
            ctx.reply(formats.describe(sb, mode, seconds))
    ctx.phase("plan", "Planning the video" if mode != "tutorial" else "Planning the steps")
    prompt = f"Task: {goal}\n\n{ref}".strip()
    if sb:
        prompt += f"\n\n{formats.board_text(sb)}"
    system = instructions(mode, seconds, formats.skill(mode) if seconds else "")
    if avoid:
        prompt += (f"\n\nA previous plan for this task failed when run for real: {avoid}. "
                   "Use a different approach that avoids that failure.")  # fmt: skip
    for _ in range(3):
        p, _res = await ctx.llm.parse(
            TutorialPlan, purpose="tutorial.plan", model=ctx.settings.research_model,
            instructions=system, input=prompt,
            reasoning_effort=ctx.settings.plan_reasoning or None,
        )  # fmt: skip
        if not p.feasible:
            return p.reason or "That task can't be shown in a Linux terminal and browser."
        issues = await _checked(ctx, p, mode, brief, seconds)
        for _ in range(RETELLS):  # wording only: a quick narration rewrite, not a new plan
            if not issues or not all(SPOKEN_ISSUE.search(x) for x in issues):
                break
            ctx.thinking("Plan", f"{'; '.join(issues)}; rewriting just the narration")
            if not await retell(ctx, p, issues, mode, brief, seconds):
                break
            issues = await _checked(ctx, p, mode, brief, seconds)
        ctx.thinking("Plan", "; ".join(issues) or f"{len(p.steps)} steps: {p.title}")
        if not issues:
            return _sized(to_spec(p, goal, mode), seconds, sb)
        prompt += f"\n\nYour previous plan had problems, fix them: {'; '.join(issues)}"
    return f"I couldn't produce a safe, runnable plan ({'; '.join(issues)})."


RETELLS = 2


def _sized(
    spec: dict[str, Any], seconds: int | None, sb: formats.Storyboard | None
) -> dict[str, Any]:
    if seconds:
        spec["seconds"] = seconds
        if spec["mode"] == "influencer":
            spec["kicker"] = f"EXPLAINED IN {seconds} SECONDS"
        elif spec["mode"] == "explainer":
            spec["kicker"] = "EXPLAINED"
    if sb:
        spec["storyboard"] = sb.model_dump()
    return spec


SPOKEN_ISSUE = re.compile(
    rf"^(narration (is \d+ words|not backed by the source)|step \S+: {FIRST_PERSON_ISSUE})"
)

RETELL = """You rewrite only the spoken narration of a tutorial video plan; the steps, their
order and what they do stay exactly as they are. Fix the problems listed, keep the same
voice and style, and say only what the steps really show (and, given a source brief,
only facts from it). Narration is read by a text-to-speech voice: short sentences, no
markdown, names written the way they're said. Return every step's narration (and its
`after` line, '' if none) by step id, plus the intro and outro."""


class SpokenLine(BaseModel):
    id: str
    narration: str = Field(description="2 short spoken sentences while the step runs")
    after: str = Field(description="1 sentence after a slow command ends; usually ''")


class Retold(BaseModel):
    """LLM contract: new narration for an otherwise unchanged plan."""

    intro: str
    outro: str
    steps: list[SpokenLine]


async def _checked(
    ctx: JobContext, p: TutorialPlan, mode: Mode, brief: str, seconds: int | None = None
) -> list[str]:
    issues = problems(p, mode, seconds)
    if brief and not issues:
        issues = await ungrounded(ctx, p, brief)
    return issues


async def retell(
    ctx: JobContext,
    p: TutorialPlan,
    issues: list[str],
    mode: Mode,
    brief: str,
    seconds: int | None = None,
) -> bool:
    """Rewrite `p`'s narration in place to fix wording-only `issues` (length, unsupported
    claims) with one quick call; False when the answer doesn't fit the plan."""
    lo, hi = words(mode, seconds)
    need = f"{lo}-{hi}" if hi else f"at least {lo}"
    ask = (f"Problems: {'; '.join(issues)}\nTotal spoken words needed: {need}.\n\n"
           f"{'BRIEF:' + chr(10) + brief + chr(10) * 2 if brief else ''}"
           f"PLAN:\nintro: {p.intro}\n{describe_steps(p)}\noutro: {p.outro}")  # fmt: skip
    try:
        r, _ = await ctx.llm.parse(
            Retold, purpose="tutorial.retell", model=ctx.settings.research_model,
            instructions=RETELL, input=ask, timeout=REPAIR_TIMEOUT,
            reasoning_effort=ctx.settings.fix_reasoning or None,
        )  # fmt: skip
    except Exception as exc:
        ctx.thinking("Plan", f"narration rewrite failed ({short(str(exc))})")
        return False
    by_id = {s.id: s for s in r.steps}
    if set(by_id) != {s.id for s in p.steps} or not r.intro.strip() or not r.outro.strip():
        return False
    p.intro, p.outro = r.intro, r.outro
    for s in p.steps:
        s.narration, s.after = by_id[s.id].narration, by_id[s.id].after
    return True


def describe_steps(p: TutorialPlan) -> str:
    return "\n".join(
        f"{s.id} ({s.kind}: {s.command or s.url or s.target or s.text or s.keys}): "
        f"narration={s.narration!r} after={s.after!r}"
        for s in p.steps
    )


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
    seen = " | ".join(x.strip() for x in text.splitlines() if x.strip())[:1500]
    rec.ok, rec.error = False, f"{expect!r} is not visible in {app} afterwards. Visible: {seen}"
    return rec


def fixed(spec: dict[str, Any], i: int, n_before: int) -> str:
    """The step(s) a repair of step `i` left in its place, for the healing note."""
    if len(spec["steps"]) < n_before:
        return "nothing: an earlier step already did it, so the step was dropped"
    return " → ".join(
        f"`{what(s)}`" for s in spec["steps"][i : i + 1 + len(spec["steps"]) - n_before]
    )


def what(step: dict[str, Any]) -> str:
    a = step["action"]
    return (a.get("command") or a.get("url") or a.get("keys") or a.get("text")
            or a.get("target") or a["kind"])  # fmt: skip


def failure(step: dict[str, Any], rec: ActionRecord | dict[str, Any]) -> tuple[str, str]:
    """Why a step failed, and what it printed."""
    get = rec.get if isinstance(rec, dict) else lambda k: getattr(rec, k)
    out = "\n".join(output_lines(rec, step["action"].get("command") or ""))
    return get("error") or f"exit code {get('returncode')}", out


def off_screen(s: PlannedStep, visible: list[str]) -> str | None:
    """What a GUI step aims at first, when that isn't among the visible elements."""
    if s.kind not in ("click", "double_click", "menu", "type") or not s.target.strip():
        return None
    aim = s.target.split(">")[0].strip().lower() if s.kind == "menu" else s.target.strip().lower()
    return None if any(aim in v or v in aim for v in visible) else aim


async def repair(
    ctx: JobContext, spec: dict[str, Any], i: int, rec: ActionRecord | dict[str, Any]
) -> str | None:
    """Rewrite failing step `i` in place from its real output. None when a fix was made,
    else why the step can't work here. Narration lines of the step are regenerated; steps
    the plan was missing before it are inserted (the caller re-runs from step `i`)."""
    step = spec["steps"][i]
    why, out = failure(step, rec)  # GUI failures carry the visible elements in `why`
    if ctx.llm is None or not ctx.llm.configured:
        return f"step **{step['label']}** failed ({why}) and no model is configured to fix it"
    goal = spec.get("goal") or spec["title"]
    ask = (f"Task: {goal}\nPlan:\n{describe_plan(spec)}\n\nFailed step {i + 1} "
           f"({step['id']}): {step}\nFailure: {why}\nOutput:\n{out[-1500:]}")  # fmt: skip
    prev = spec["steps"][i - 1]["action"] if i else None
    if "No element matching" in why and CONFIRM.match(step["action"].get("target") or ""):
        ask += (
            f"\n\nThe step before it ({prev}) ran and passed its check, and no "
            f"{step['action']['target']!r} button appeared. Many apps apply a change at once "
            "with no confirm button; if that is the case here, set `drop`."
        )
    elif "covered by" in why:
        ask += (
            "\n\nThe element is there but something is open over it, so a click would land "
            "on that instead. Put the click that closes it (one of its listed buttons) in "
            "`before`, unless the step needs that dialog."
        )
    elif "No element matching" in why:
        ask += (
            "\n\nThe element isn't on screen at all, so aiming at it differently won't help. "
            "From the Visible list, find what has to be clicked first to reach it (a start "
            "or continue button, a menu, a tab, a dialog) and put that in `before`."
        )
    if said := [spec["lines"][k] for s in spec["steps"] for k in s["narration"][:1]
                if k in spec["lines"] and s["id"] != step["id"]][:3]:  # fmt: skip
        ask += ("\n\nWrite the narration in the same voice and length as the plan's other "
                "lines, talking to the viewer (never 'I will' or 'let me'), e.g.:\n"
                + "\n".join(f"- {x}" for x in said))  # fmt: skip
    visible = [v.strip().lower() for v in why.partition("Visible: ")[2].split(";") if v.strip()]
    for _ in range(3):
        fix, _ = await ctx.llm.parse(
            StepFix, purpose="tutorial.repair", model=ctx.settings.research_model,
            instructions=FIX, input=ask, timeout=REPAIR_TIMEOUT,
            reasoning_effort=ctx.settings.fix_reasoning or None,
        )  # fmt: skip
        if any(b.kind == "click" and CONSENT.search(b.target) for b in (*fix.before, fix.step)):
            ask += (
                "\n\nCookie and consent banners are closed automatically before every "
                "browser step; don't click them. Fix the step another way."
            )
        elif prev and any(action(b) == prev for b in fix.before):
            ask += (
                "\n\nYour `before` repeats the previous step, which already ran and passed; "
                "running it again would do it twice. Use a different step, or `drop`."
            )
        elif any(FIRST_PERSON.search(b.narration) for b in (*fix.before, fix.step)):
            ask += (
                "\n\nYour narration speaks as the operator ('I will ...'). Rewrite it to "
                "talk to the viewer about what the screen shows, like the plan's other lines."
            )
        elif fix.before or fix.drop or not fix.fixable:
            break
        elif action(fix.step) == step["action"]:
            ask += (
                "\n\nYour fix was the same step, which just failed exactly like that. "
                "Change it, or add the missing steps that must come first in `before`."
            )
        elif visible and (aim := off_screen(fix.step, visible)):
            ask += (
                f"\n\nYour fix aims at {aim!r}, which isn't on screen either. Aim at one of "
                "the visible elements, or put the steps that bring it on screen in `before`."
            )
        else:
            break
    for s in (*fix.before, fix.step):
        s.narration = viewer_voice(s.narration)
    gui = step["action"]["kind"] in GUI and len(spec["steps"]) > 1
    if fix.drop and fix.fixable and gui and "No element matching" in why:
        del spec["steps"][i]  # only a target that isn't there at all marks a step unneeded
        for k in (step["id"], f"{step['id']}_done"):
            spec["lines"].pop(k, None)
        return None
    fix.step.id = step["id"]
    ids = {s["id"] for s in spec["steps"]}
    pre = fix.before[:2] if len(spec["steps"]) + len(fix.before) <= MAX_STEPS + 4 else []
    for p in pre:
        p.id = next(
            f"{step['id']}_pre{n}" for n in range(1, 99) if f"{step['id']}_pre{n}" not in ids
        )
        ids.add(p.id)
        if spec.get("storyboard") and p.pace == "real":
            p.pace = "fast"  # steps a repair adds only get to the beat; don't dwell on them
    bad = [x for s in (*pre, fix.step) for x in problems_one(s)]
    if not fix.fixable or bad:
        return f"step **{step['label']}** can't work here: {fix.reason or '; '.join(bad)}"
    new = to_spec(_stub([*pre, fix.step]), goal)
    spec["steps"][i : i + 1] = new["steps"]
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
    state: dict[str, Any] = {"repairs": 0}
    for attempt in range(1, SANDBOX_TRIES + 1):
        try:
            return await _rehearse(ctx, spec, heal, state)
        except Exception as exc:
            if attempt == SANDBOX_TRIES or isinstance(exc, SandboxLimit):
                raise
            heal(f"The rehearsal sandbox broke ({short(exc)}); starting a fresh one")
    raise AssertionError("unreachable")


async def _rehearse(
    ctx: JobContext, spec: dict[str, Any], heal: Healer, state: dict[str, Any]
) -> str | None:
    mgr = ctx._manager.sandboxes
    ctx.phase("rehearse", "Rehearsing every step in a private sandbox (not recorded)")
    async with mgr.session(ctx.id, ctx.conversation_id) as (sid, box):
        ctx.emit("sandbox.started", {"sandbox_id": sid, "live_url": live_path(ctx.id)})
        await put_files(box, spec)
        err, _ = await settle(ctx, spec, heal, state, sid, box, 0)
        if err:
            return err
        await mgr.stop(sid, "rehearsal passed")
    return None


async def settle(
    ctx: JobContext, spec: dict[str, Any], heal: Healer, state: dict[str, Any],
    sid: str, box: Sandbox, i: int | None,
) -> tuple[str | None, bool]:  # fmt: skip
    """Finish a rehearsal in `box`: run steps `i`.. repairing failures (None: every step
    already ran), then look at the result and add what's missing. (why it can't be done
    or None, whether the review changed the plan). The sandbox is stopped on failure."""
    mgr, changed = ctx._manager.sandboxes, False
    while True:
        if i is not None and (err := await _run_from(ctx, spec, heal, state, sid, box, i)):
            return err, changed
        if spec.get("mode") != "tutorial":  # a walkthrough: every step already checked
            break  # what it shows; extra end steps would push it past a minute
        async with mgr.busy(sid):
            found = await review(ctx, box, spec)
        if found is None:
            break
        i, missing, added = found
        if not added or state.setdefault("reviews", 0) >= MAX_REVIEWS:
            await mgr.stop(sid, "rehearsal failed")
            return f"the finished result doesn't show: {'; '.join(missing)}", changed
        state["reviews"] += 1
        changed = True
        steps = " → ".join(f"`{what(s)}`" for s in spec["steps"][i : i + added])
        heal(f"Rehearsal: every step passed, but the result didn't show "
             f"{'; '.join(missing)}; added {steps}")  # fmt: skip
    spec["rehearsal"] = {"repairs": state["repairs"], "reviews": state.get("reviews", 0),
                         "sandbox_id": sid}  # fmt: skip
    return None, changed


async def _run_from(
    ctx: JobContext, spec: dict[str, Any], heal: Healer, state: dict[str, Any],
    sid: str, box: Sandbox, i: int,
) -> str | None:  # fmt: skip
    """Run steps `i`.. in order, repairing failures in place; None when all passed."""
    mgr = ctx._manager.sandboxes
    while i < len(spec["steps"]):
        step = spec["steps"][i]
        ctx.phase("rehearse", f"Rehearsing step {i + 1}/{len(spec['steps'])}: {step['label']}")
        mgr.touch(sid)
        async with mgr.busy(sid):
            rec = await check(box, step, await box.run(i, Action.model_validate(step["action"])))
        mgr.touch(sid)
        if rec.popups:
            ctx.thinking("Rehearsal", f"Closed a cookie/consent banner ({', '.join(rec.popups)})")
        if rec.ok:
            i += 1
            continue
        why, out = failure(step, rec)  # GUI failures carry the visible elements in `why`
        ctx.thinking("Rehearsal", f"Step {step['id']} failed: {why}\n{out[-600:]}")
        brief = why.split(" Visible:")[0]  # the chat gets the reason, the model all of it
        tries = state.setdefault(step["id"], 0)
        if tries >= MAX_REPAIRS or state["repairs"] >= MAX_TOTAL_REPAIRS:
            await mgr.stop(sid, "rehearsal failed")
            return f"step **{step['label']}** still fails after {tries} fixes ({brief})"
        state["repairs"] += 1
        state[step["id"]] = tries + 1
        ctx.phase("plan", f"Fixing step {i + 1} from its real output")
        n = len(spec["steps"])
        async with mgr.busy(sid):  # the sandbox waits for the fix; no idle shutdown
            err = await repair(ctx, spec, i, rec)
        if err:
            await mgr.stop(sid, "rehearsal failed")
            return err
        heal(f"Rehearsal: step **{step['label']}** failed ({brief}); rewrote it from the "
             f"real output as {fixed(spec, i, n)}")  # fmt: skip
    return None


async def _image(box: Sandbox, path: str) -> dict[str, str] | None:
    """`path` in the sandbox as a small JPEG for the model, or None if it can't be read."""
    res = await box.exec(
        f"ffmpeg -y -loglevel error -i {shlex.quote(path)} -vf "
        "'scale=1280:1280:force_original_aspect_ratio=decrease' -q:v 4 /tmp/cf/look.jpg",
        60,
    )
    if res.returncode != 0:
        return None
    data = base64.b64encode(await box.read_file("/tmp/cf/look.jpg")).decode()
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}"}


async def review(
    ctx: JobContext, box: Sandbox, spec: dict[str, Any]
) -> tuple[int, list[str], int] | None:
    """Look at the finished result as a viewer would. When something asked for doesn't
    show, insert the steps that add it: (where to resume, what was missing, how many steps
    were added; 0 when the model's steps were invalid and nothing was added)."""
    if ctx.llm is None or not ctx.llm.configured:
        return None
    goal = spec.get("goal") or spec["title"]
    if spec["steps"] and spec["steps"][-1]["action"]["kind"] == "terminal":
        gui = [s["action"] for s in spec["steps"] if s["action"]["kind"] != "terminal"]
        if gui:  # a closing terminal check covers the result; look at the app instead
            a = gui[-1]
            app = (a.get("command") or "").split(" ")[0] if a["kind"] == "launch" else (
                a.get("app") or "browser")  # fmt: skip
            await box.exec(FOCUS.get(app) or f"/opt/cf/focus.sh {shlex.quote(app)}", 20)
    await box.screenshot()
    content: list[dict[str, str]] = [
        {"type": "input_text", "text": f"Task: {goal}\nPlan:\n{describe_plan(spec)}"},
        {"type": "input_text", "text": "Final screen:"},
    ]
    if shot := await _image(box, "/tmp/cf/shot.png"):
        content.append(shot)
    res = await box.exec("ls -t /root/Downloads/* 2>/dev/null | grep -iE '\\.(png|jpe?g|webp)$' "
                         "| head -1", 20)  # fmt: skip
    f = res.stdout.strip()
    if f.startswith("/root/Downloads/") and (img := await _image(box, f)):
        content += [{"type": "input_text", "text": f"Exported file {f}:"}, img]
    out, _ = await ctx.llm.parse(
        Outcome, purpose="tutorial.review", model=ctx.settings.research_model,
        instructions=REVIEW, input=[{"role": "user", "content": content}],
        timeout=REVIEW_TIMEOUT, reasoning_effort=ctx.settings.plan_reasoning or None,
    )  # fmt: skip
    if out.done:
        return None
    ids = [s["id"] for s in spec["steps"]]
    if out.after in ids:
        at = ids.index(out.after) + 1
    else:  # before the first export/save, else at the end
        at = next((k for k, s in enumerate(spec["steps"])
                   if re.search(r"export|save", what(s), re.I)), len(ids))  # fmt: skip
    add = out.steps[:4] if len(ids) + len(out.steps[:4]) <= MAX_STEPS + 8 else []
    for n, p in enumerate(add, 1):
        p.id = next(f"look{n}_{k}" for k in range(1, 99) if f"look{n}_{k}" not in ids)
        ids.append(p.id)
    bad = [x for p in add for x in problems_one(p)]
    missing = out.missing or ["what the task asks for"]
    if not add or bad:
        return 0, [*missing, *(f"(the steps to add it were invalid: {b})" for b in bad[:2])], 0
    new = to_spec(_stub(add), goal)
    spec["steps"][at:at] = new["steps"]
    spec["lines"].update({k: v for k, v in new["lines"].items() if k not in ("intro", "outro")})
    return at, missing, len(add)


MAX_FILE_MB = 25


def attached(ctx: JobContext) -> list[dict[str, Any]]:
    """The user's uploads on the request message that can go into the sandbox."""
    root = ctx.settings.uploads_dir
    out = []
    for u in ctx.input.get("attachments") or []:
        path = root / u["id"] / u["filename"]
        if path.is_file() and u.get("size", 0) <= MAX_FILE_MB * 1024 * 1024:
            out.append({"name": u["filename"], "upload_id": u["id"], "kind": u.get("kind"),
                        "content_type": u.get("content_type"), "path": str(path)})  # fmt: skip
    return out


def with_files(goal: str, files: list[dict[str, Any]]) -> str:
    if not files:
        return goal
    names = ", ".join(f"/work/{f['name']} ({f['content_type']})" for f in files)
    return f"{goal}\n\nThe user attached these files; use them: {names}"


async def put_files(box: Sandbox, spec: dict[str, Any]) -> None:
    """Copy the user's attached files into /work, exactly as uploaded."""
    from pathlib import Path

    for f in spec.get("files") or []:
        path = Path(f["path"])
        if not path.is_file():
            raise JobFailed(f"The attached file {f['name']} is no longer on the server")
        await box.write_file(f"/work/{f['name']}", path.read_bytes())


def _stub(steps: list[PlannedStep]) -> TutorialPlan:
    blank = dict.fromkeys(("reason", "title", "subtitle", "summary", "software", "done"), "")
    return TutorialPlan(feasible=True, tags=[], intro="", outro="", say=[], steps=steps, **blank)


def problems_one(step: PlannedStep) -> list[str]:
    return [
        p for p in problems(_stub([step, step]))
        if "unique" not in p and "words" not in p and FIRST_PERSON_ISSUE not in p
    ]  # fmt: skip
