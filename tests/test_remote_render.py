"""The Modal render runner (app.tutorial.remote): manifest staging, polling, and
pulling the finished files back — with a fake sandbox, no Modal or R2 calls."""

import contextlib
import json

import pytest

from app.tutorial import remote
from app.tutorial.contracts import State, Tutorial


class FakeStorage:
    backend = "r2"

    def ref(self, key: str) -> dict:
        return {"backend": "r2", "key": f"cueframe/{key}"}

    async def get(self, stored: dict) -> bytes:
        return b"data"

    async def put(self, key: str, data: bytes, content_type: str) -> dict:
        return {"backend": "r2", "key": f"cueframe/{key}", "url": "/x", "bytes": len(data)}


class FakeBox:
    def __init__(self):
        self.manifest: dict | None = None
        self.polls = 0

    async def write_file(self, path: str, data: bytes) -> None:
        assert path == "/work/manifest.json"
        self.manifest = json.loads(data)

    async def exec(self, script: str, timeout: float = 60):
        class R:
            returncode = 0
            stderr = ""

        r = R()
        if "nohup" in script:
            r.stdout = "started"
        elif "__P__" in script:
            self.polls += 1
            if self.polls == 1:  # still rendering: progress only
                r.stdout = (f"__P__\n{json.dumps({'done': 1, 'total': 2})}\n__R__\n\n"
                            "__F__\n\n__S__\nRUNNING")
            else:
                result = {
                    "stats": {"duration": 2.0, "states": 1, "rendered": 1,
                              "render_seconds": 3.0},
                    "qa_issues": [],
                    "out": {"video": "k/tutorial.mp4", "captions": "k/captions.srt",
                            "thumbnail": "k/thumbnail.png"},  # fmt: skip
                }
                r.stdout = (f"__P__\n{json.dumps({'done': 2, 'total': 2})}\n__R__\n"
                            f"{json.dumps(result)}\n__F__\n\n__S__\nGONE")
        return r


class FakeSandboxes:
    def __init__(self, box):
        self.box = box
        self.settings_seen = None

    @contextlib.asynccontextmanager
    async def session(self, job_id, conversation_id, settings=None):
        self.settings_seen = settings
        yield "sb_1", self.box

    @contextlib.asynccontextmanager
    async def busy(self, sid):
        yield


class FakeManager:
    def __init__(self, box):
        self.storage = FakeStorage()
        self.sandboxes = FakeSandboxes(box)


class FakeCtx:
    def __init__(self, settings, box):
        self.settings = settings
        self._manager = FakeManager(box)
        self.id = "j_1"
        self.conversation_id = None

    def thinking(self, *a):
        pass


def _ctx(settings, box):
    return FakeCtx(settings, box)


def _row():
    return {
        "spec": {"steps": []},
        "voice": {"a": {"file": "l1.mp3",
                        "stored": {"backend": "r2", "key": "cueframe/tutorials/t_1/l1.mp3"}}},
        "capture": {"recording": {"backend": "r2", "key": "cueframe/tutorials/t_1/rec.mp4"}},
    }


def _tut():
    return Tutorial(id="t_1", title="T", states=[State(id="s1", duration=2.0)])


@pytest.mark.anyio
async def test_remote_render_stages_polls_and_pulls(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "POLL_SECONDS", 0)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "test")
    box = FakeBox()
    ctx = _ctx(settings, box)
    seen = []
    res = await remote.render_modal(
        ctx, _row(), _tut(), tmp_path, lambda d, t, s=None: seen.append((d, t)), force={"s1"}
    )
    man = box.manifest
    assert man["tid"] == "t_1" and man["force"] == ["s1"]
    rels = {rel for _, rel in man["assets"]}
    assert "recording.mp4" in rels and "audio/l1.mp3" in rels
    assert {f"audio/sfx_{k}.mp3" for k in remote.SFX} <= rels
    assert all(key.startswith("cueframe/") for key, _ in man["assets"])
    # the sandbox got the render image and the render sizing
    cfg = ctx._manager.sandboxes.settings_seen
    assert cfg.sandbox_image == "render" and cfg.sandbox_cpu == settings.render_cpu
    assert res["qa_issues"] == [] and res["stats"]["render_seconds"] == 3.0
    assert (tmp_path / "out" / "tutorial.mp4").read_bytes() == b"data"
    assert seen == [(1, 2)]  # progress forwarded while the sandbox was still running


@pytest.mark.anyio
async def test_remote_render_needs_r2(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "POLL_SECONDS", 0)
    ctx = _ctx(settings, FakeBox())
    ctx._manager.storage.backend = "local"
    with pytest.raises(RuntimeError, match="R2"):
        await remote.render_modal(ctx, _row(), _tut(), tmp_path, lambda d, t, s=None: None)


@pytest.mark.anyio
async def test_remote_render_surfaces_a_crash(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "POLL_SECONDS", 0)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "test")

    class CrashingBox(FakeBox):
        async def exec(self, script: str, timeout: float = 60):
            r = await super().exec(script, timeout)
            if "__P__" in script:
                r.stdout = "__P__\n\n__R__\n\n__F__\nTraceback: boom\n__S__\nGONE"
            return r

    ctx = _ctx(settings, CrashingBox())
    with pytest.raises(RuntimeError, match="boom"):
        await remote.render_modal(ctx, _row(), _tut(), tmp_path, lambda d, t, s=None: None)
