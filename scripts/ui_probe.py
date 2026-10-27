"""Run element-targeted actions through Sandbox.run on a real 1080p Modal desktop, with the
planner's after-step checks. Screenshots go to /tmp/ui_probe. About 1-2 min of sandbox."""

import asyncio
import dataclasses
from pathlib import Path

from app.config import get_settings
from app.sandbox.contracts import Action
from app.sandbox.modal_box import ModalSandbox
from app.tutorial import planner
from app.tutorial.pipeline import DESKTOP

OUT = Path("/tmp/ui_probe")
STEPS = [
    ("open", {"kind": "open_url", "url": "https://docs.python.org/3/", "app": "browser"}, ""),
    (
        "search",
        {
            "kind": "type",
            "target": "Quick search",
            "text": "asyncio",
            "enter": True,
            "app": "browser",
        },
        "search results",
    ),
    ("missing", {"kind": "click", "target": "Nonexistent button", "app": "browser"}, ""),
    (
        "install",
        {
            "kind": "terminal",
            "command": "DEBIAN_FRONTEND=noninteractive apt-get "
            "install -y -q gnome-calculator > /dev/null && echo done",
            "ask": False,
        },
        "done",
    ),
    ("launch", {"kind": "launch", "command": "gnome-calculator", "app": "gnome-calculator"}, ""),
    ("seven", {"kind": "click", "target": "7", "app": "gnome-calculator"}, ""),
    ("plus", {"kind": "click", "target": "Add", "app": "gnome-calculator"}, ""),
    ("five", {"kind": "click", "target": "5", "app": "gnome-calculator"}, ""),
    ("equals", {"kind": "click", "target": "Calculate result", "app": "gnome-calculator"}, "12"),
    ("back", {"kind": "click", "target": "asyncio", "app": "browser"}, "asyncio"),
]


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    s = dataclasses.replace(get_settings(), **DESKTOP)
    box = await ModalSandbox.create(s, "about:blank")
    try:
        await asyncio.sleep(3)
        for i, (sid, a, expect) in enumerate(STEPS):
            step = {"id": sid, "action": a, "expect": expect}
            rec = await planner.check(box, step, await box.run(i, Action.model_validate(a)))
            print(
                f"--- {sid}: ok={rec.ok} box={rec.box} {rec.settled - rec.started:.1f}s "
                f"err={(rec.error or '')[:500]}"
            )
            (OUT / f"{i:02d}_{sid}.png").write_bytes(await box.screenshot())
        r = await box.exec("/opt/cf/ui.py text gnome-calculator | head -c 1500")
        print("calc tree:", r.stdout.replace("\n", " | "))
    finally:
        await box.terminate()
        await ModalSandbox.release(s, [box.provider_id])
        print("terminated", box.provider_id)


asyncio.run(main())
