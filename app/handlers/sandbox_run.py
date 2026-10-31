"""`sandbox.run`: run a list of desktop/browser/terminal actions in a fresh sandbox
while recording one continuous video, then reply with the recording and a
timestamped action log (the raw material for per-state tutorial clips)."""

import re
from typing import Any

from app.jobs import JobContext, JobFailed, job_handler
from app.sandbox.contracts import Action, ActionRecord, live_path

SKIP = "Already installed - leave it out of the video"
SHOW = "Not installed - show the install step"


def _plan(ctx: JobContext) -> tuple[str, list[Action]]:
    spec = ctx.input.get("job_input") or {}
    actions = [Action.model_validate(a) for a in spec.get("actions", [])]
    start = spec.get("start_url") or "about:blank"
    if not actions:
        m = re.search(r"https?://\S+", ctx.input.get("text", ""))
        if not m:
            raise ValueError("sandbox.run needs job_input.actions or a URL in the message")
        actions = [Action(kind="open_url", url=m.group(0)), Action(kind="wait", seconds=2)]
    return start, actions


@job_handler("sandbox.run")
async def sandbox_run(ctx: JobContext) -> dict[str, Any]:
    start, actions = _plan(ctx)
    hidden: set[int] = set()
    ctx.phase("intake", "Checking with you before running terminal commands")
    for i, a in enumerate(actions):
        if a.kind == "terminal" and a.ask:
            ans = await ctx.ask(
                f"Step {i + 1} runs `{a.command}` in a terminal. "
                "Is it already set up on your side?",
                [SKIP, SHOW],
                key=f"step{i}",
            )
            if ans == SKIP:
                hidden.add(i)
    mgr, storage = ctx._manager.sandboxes, ctx._manager.storage
    ctx.phase("sandbox", "Starting sandbox desktop")
    ctx.progress(0.05)
    records: list[ActionRecord] = []
    async with mgr.session(ctx.id, ctx.conversation_id, start) as (sid, box):
        ctx.emit("sandbox.started", {"sandbox_id": sid, **(
            {"live_url": live_path(ctx.id)} if ctx.settings.live_view else {})})
        t0 = await box.start_recording()
        for i, a in enumerate(actions):
            ctx.phase("act", f"Step {i + 1}/{len(actions)}: {a.caption or a.kind}")

            async def out(line: str, i: int = i) -> None:
                mgr.touch(sid)
                ctx.emit("sandbox.output", {"sandbox_id": sid, "step": i, "line": line})

            mgr.touch(sid)
            async with mgr.busy(sid):
                rec = await box.run(i, a, out)
            mgr.touch(sid)
            rec.in_video = i not in hidden
            rec.t_in = max(0.0, rec.started - t0 - 0.3)
            rec.t_out = max(rec.t_in, rec.settled - t0 + 0.5)
            shot = await storage.put(f"jobs/{ctx.id}/step{i:02d}.png", await box.screenshot(),
                                     "image/png")  # fmt: skip
            rec.screenshot_url = shot["url"]
            records.append(rec)
            ctx.emit("sandbox.action", {"sandbox_id": sid, "record": rec.model_dump()})
            ctx.progress(0.1 + 0.8 * (i + 1) / len(actions))
            if not rec.ok:
                break
        ctx.phase("upload", "Saving the recording")
        video = await storage.put(f"jobs/{ctx.id}/recording.mp4", await box.stop_recording(),
                                  "video/mp4")  # fmt: skip
        failed = next((r for r in records if not r.ok), None)
        await mgr.stop(sid, "job failed" if failed else "job finished")
    rows = "\n".join(
        f"| {r.index + 1} | {r.action.caption or r.action.kind} | {'ok' if r.ok else 'FAILED'} "
        f"| {r.t_in:.1f}-{r.t_out:.1f}s | {'yes' if r.in_video else 'hidden'} |"
        for r in records
    )
    ctx.reply(
        f"{'**Run failed at step ' + str(failed.index + 1) + ':** ' + (failed.error or '') if failed else '**Sandbox run finished.**'}\n\n"  # noqa: E501
        f'<video src="{video["url"]}" controls></video>\n\n'
        "| # | Step | Result | Clip window | In video |\n|---|---|---|---|---|\n" + rows
    )
    result = {"sandbox_id": sid, "recording": video, "actions": [r.model_dump() for r in records]}
    if failed:
        raise JobFailed(f"Step {failed.index + 1} failed: {failed.error}", result)
    return result
