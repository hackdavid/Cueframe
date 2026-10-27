"""Run a tutorial spec for real in a Modal sandbox while recording one continuous
1080p video. After each step it holds the screen for as long as that step's
narration needs, so the renderer can cut without freezing frames.

    CUEFRAME_MODAL_PROFILE=ibrahimdaud03 uv run python scripts/capture_tutorial.py \
        tutorials/nginx-website build/nginx-website
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.db import Store
from app.log import setup_logging
from app.sandbox.contracts import Action
from app.sandbox.manager import SandboxManager


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return float(out.stdout)


async def main(spec_dir: Path, out: Path) -> None:
    settings = get_settings()
    setup_logging()
    spec = json.loads((spec_dir / "spec.json").read_text())
    audio = out / "audio"
    store = Store(out / "capture.db")
    mgr = SandboxManager.from_settings(store, settings)
    job = store.create_job(None, "sandbox.run", {}, None)
    records = []
    async with mgr.session(job["id"], None, "about:blank") as (sid, box):
        await box.run(-1, Action(kind="terminal", command="clear", ask=False))  # open the terminal
        t0 = await box.start_recording()
        await asyncio.sleep(1.0)
        for i, step in enumerate(spec["steps"]):
            action = Action.model_validate(step["action"])
            need = sum(duration(audio / f"{k}.mp3") for k in step["narration"]) + 0.6 * len(
                step["narration"]
            )
            async with mgr.busy(sid):
                rec = await box.run(i, action)
            spent = rec.settled - rec.started
            hold = max(1.2, need - spent + 0.8)
            await asyncio.sleep(hold)
            rec.t_in = rec.started - t0
            rec.t_out = rec.settled - t0 + hold
            records.append({"step": step["id"], **rec.model_dump()})
            print(f"{step['id']}: ok={rec.ok} run={spent:.1f}s hold={hold:.1f}s {rec.error or ''}")
            if not rec.ok:
                break
        (out / "recording.mp4").write_bytes(await box.stop_recording())
        await mgr.stop(sid, "capture finished")
    (out / "capture.json").write_text(json.dumps({"t0": t0, "records": records}, indent=1))
    print("sandbox:", store.get_sandbox(sid))


if __name__ == "__main__":
    t = time.monotonic()
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
    print(f"done in {time.monotonic() - t:.0f}s")
