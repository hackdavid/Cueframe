"""Check element finding on a real 1080p Modal desktop: browser (DevTools) and a GTK
app (AT-SPI). Saves screenshots with the found boxes to /tmp/ui_probe. ~1 min of sandbox."""

import asyncio
import dataclasses
import shlex
from pathlib import Path

from app.config import get_settings
from app.sandbox.modal_box import ModalSandbox
from app.tutorial.pipeline import DESKTOP

OUT = Path("/tmp/ui_probe")


async def step(box, label, script, timeout=120):
    r = await box.exec("export DISPLAY=:99; " + script, timeout)
    print(f"--- {label} rc={r.returncode}\n{(r.stdout + r.stderr)[-700:]}")
    return r


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    s = dataclasses.replace(get_settings(), **DESKTOP)
    box = await ModalSandbox.create(s, "https://docs.python.org/3/")
    try:
        await asyncio.sleep(4)
        for t, mode in [("Tutorial", "click"), ("Quick search", "type")]:
            r = await step(box, f"find {t}", f"/opt/cf/ui.py find browser {shlex.quote(t)} {mode}")
            if "__CFBOX" in r.stdout:
                x, y, w, h = r.stdout.split("__CFBOX")[1].split()[:4]
                await step(
                    box, "mark", f"/opt/cf/move.sh {int(x) + int(w) // 2} {int(y) + int(h) // 2}"
                )
                (OUT / f"browser_{mode}.png").write_bytes(await box.screenshot())
                print("box", x, y, w, h)
        await step(box, "text", "/opt/cf/ui.py text browser | head -c 300")
        await step(
            box,
            "install",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -q mousepad >/tmp/apt.log 2>&1; echo $?",
            300,
        )
        await step(box, "launch", "/opt/cf/launch.sh mousepad")
        await asyncio.sleep(2)
        (OUT / "app.png").write_bytes(await box.screenshot())
        for t, mode in [("File", "click"), ("Search", "click"), ("x", "type")]:
            r = await step(
                box, f"app find {t}", f"/opt/cf/ui.py find mousepad {shlex.quote(t)} {mode}"
            )
        await step(box, "app text", "/opt/cf/ui.py text mousepad | head -c 400")
    finally:
        await box.terminate()
        await ModalSandbox.release(s, [box.provider_id])
        print("terminated", box.provider_id)


asyncio.run(main())
