"""Live smoke test of the Modal sandbox provider through SandboxManager.

    CUEFRAME_MODAL_PROFILE=ibrahimdaud03 uv run python scripts/sandbox_smoke.py

Boots a desktop, records continuously, opens a page, runs a visible terminal
command, saves the recording + screenshots to /tmp/cf-smoke, and always terminates.
"""

import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.db import Store
from app.log import setup_logging
from app.sandbox.contracts import Action
from app.sandbox.manager import SandboxManager

OUT = Path("/tmp/cf-smoke")
STEPS = [
    Action(kind="open_url", url="https://docs.python.org/3/tutorial/", caption="open"),
    Action(kind="scroll", x=640, y=400, dy=3, caption="scroll"),
    Action(
        kind="terminal", command="apt-get install -y cowsay && /usr/games/cowsay hi", timeout=240
    ),
    Action(kind="terminal", command="for i in 1 2 3 4 5 6; do echo tick $i; sleep 2; done"),
]


async def main() -> None:
    settings = get_settings()
    setup_logging()
    OUT.mkdir(exist_ok=True)
    store = Store(OUT / "smoke.db")
    mgr = SandboxManager.from_settings(store, settings)
    job = store.create_job(None, "sandbox.run", {}, None)
    t = time.monotonic()
    async with mgr.session(job["id"], None) as (sid, box):
        print("booted in", round(time.monotonic() - t, 1), "s; live:", box.live_url)
        t0 = await box.start_recording()
        recs = []
        for i, a in enumerate(STEPS):

            async def out(line: str) -> None:
                print("   >", line)

            rec = await box.run(i, a, out)
            rec.t_in, rec.t_out = rec.started - t0 - 0.3, rec.settled - t0 + 0.5
            (OUT / f"step{i}.png").write_bytes(await box.screenshot())
            recs.append(rec)
            print(f"step {i} {a.kind}: ok={rec.ok} rc={rec.returncode} "
                  f"run={rec.ended - rec.started:.2f}s settle={rec.settled - rec.ended:.2f}s "
                  f"window={rec.t_in:.1f}-{rec.t_out:.1f}s err={rec.error}")  # fmt: skip
        (OUT / "rec.mp4").write_bytes(await box.stop_recording())
        (OUT / "actions.json").write_text(json.dumps([r.model_dump() for r in recs], indent=1))
    print("sandbox row:", store.get_sandbox(sid))


asyncio.run(main())
