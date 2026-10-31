"""Deterministic in-process sandbox for tests and machines without Modal."""

import itertools
import time

from app.sandbox.base import MARK, Sandbox
from app.sandbox.contracts import Action, ActionRecord, ExecResult

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cf000000030101007e2f5d0a0000000049454e44ae426082"
)
_ids = itertools.count(1)


class FakeSandbox(Sandbox):
    provider = "fake"
    # What `ui.py text APP` shows per app, and element targets that aren't on screen.
    screen: dict[str, str] = {}
    missing: set[str] = set()

    def __init__(self, fail_on: set[int] | None = None):
        self.provider_id = f"fake-{next(_ids)}"
        self.live_url = f"https://example.invalid/{self.provider_id}/vnc.html"
        self.fail_on = fail_on or set()
        self.scripts: list[str] = []
        self.actions: list[Action] = []
        self.terminated = False
        self.recording = False

    @classmethod
    async def create(cls, settings: object, start_url: str = "about:blank") -> "FakeSandbox":
        return cls()

    async def beat(self) -> None:
        """Keepalive without activity, mirroring ModalSandbox.beat."""
        if self.terminated:
            raise RuntimeError("sandbox terminated")
        self.scripts.append("true")

    async def exec(self, script: str, timeout: float = 60) -> ExecResult:
        if self.terminated:
            raise RuntimeError("sandbox terminated")
        self._note_active()
        self.scripts.append(script)
        if script == "true":
            return ExecResult(returncode=0, stdout="")
        if "http_code" in script:
            return ExecResult(returncode=0, stdout="200")
        if "/opt/cf/ui.py text" in script:
            app = next((a for a in self.screen if f"text {a} " in script), None)
            return ExecResult(returncode=0, stdout=self.screen.get(app or "", ""))
        return ExecResult(returncode=0, stdout=f"{time.time()}\n")

    async def read_file(self, path: str) -> bytes:
        return PNG_1PX if path.endswith(".png") else b"\x00\x00\x00\x18ftypmp42fake"

    async def start_recording(self) -> float:
        self.recording = True
        return time.time()

    async def stop_recording(self) -> bytes:
        self.recording = False
        return await self.read_file("/tmp/cf/rec.mp4")

    async def run(self, index, action, on_output=None) -> ActionRecord:  # type: ignore[override]
        from app.sandbox.base import _record

        self.actions.append(action)
        s = time.time()
        rc = 1 if index in self.fail_on else 0
        if action.kind == "terminal" and on_output:
            await on_output(f"$ {action.command}")
        body = "ok"
        if action.target in self.missing:
            rc, body = 3, f"No element matching {action.target!r} on screen. Visible: download"
        elif action.target:
            body = "__CFBOX 100 200 50 20\nok"
        res = ExecResult(returncode=0, stdout=f"{MARK} {s} {s + 0.4} {s + 0.6} {rc}\n{body}")
        return _record(index, action, res)

    async def terminate(self) -> None:
        self.terminated = True
