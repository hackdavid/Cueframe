import time

from app.tutorial import pipeline
from app.tutorial.contracts import Word


def _auth(c):
    return {"Authorization": f"Bearer {c.token}"}


def _wait(c, jid, statuses, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/api/jobs/{jid}", headers=_auth(c)).json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck: {job}")


def _fakes(monkeypatch):
    spoken, rendered = [], []

    async def speak(text, voice_id=None, seed=None):
        spoken.append(text)
        return b"mp3", [
            Word(text=w, t=i * 0.1, end=i * 0.1 + 0.08) for i, w in enumerate(text.split())
        ]

    async def sound(prompt, seconds, influence=0.5):
        return b"sfx"

    async def render(tut, work, fetch, save, progress):
        rendered.append(tut)
        out = work / "out"
        out.mkdir(parents=True, exist_ok=True)
        for f in ("tutorial.mp4", "captions.srt", "thumbnail.png"):
            (out / f).write_bytes(b"x")
        stats = {"duration": sum(s.duration for s in tut.states), "states": len(tut.states),
                 "rendered": len(tut.states), "render_seconds": 1.0}  # fmt: skip
        return {"video": out / "tutorial.mp4", "captions": out / "captions.srt",
                "thumbnail": out / "thumbnail.png", "stats": stats}  # fmt: skip

    monkeypatch.setattr(pipeline, "speak", speak)
    monkeypatch.setattr(pipeline, "sound", sound)
    monkeypatch.setattr(pipeline, "render", render)
    monkeypatch.setattr(pipeline, "PACE", 0)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "test")
    monkeypatch.setattr(pipeline, "DESKTOP", {})
    return spoken, rendered


def _make(client, answer):
    body = {"content": "Make a video tutorial: host my own website on Linux with nginx"}
    res = client.post("/api/messages", json=body, headers=_auth(client)).json()
    job = _wait(client, res["job"]["id"], {"waiting_input", "failed"})
    assert job["status"] == "waiting_input", job["error"]
    assert client.get("/api/sandboxes", headers=_auth(client)).json() == []  # no paid sandbox yet
    pi = job["pending_input"]
    client.post(f"/api/jobs/{job['id']}/answer", headers=_auth(client),
                json={"input_id": pi["input_id"], "value": answer})  # fmt: skip
    return res["conversation"]["id"], _wait(client, job["id"], {"succeeded", "failed"})


def test_chat_makes_tutorial_end_to_end(client, monkeypatch):
    spoken, rendered = _fakes(monkeypatch)
    cid, job = _make(client, pipeline.SHOW)
    assert job["status"] == "succeeded", job["error"]
    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    ids = [s["id"] for s in tut["graph"]["states"]]
    assert ids == ["title", "install", "start", "browser", "write", "reload", "verify", "outro"]
    assert tut["status"] == "ready" and tut["version"] == 1
    assert tut["output"]["video"]["url"].endswith("video-v1.mp4")
    assert all(
        s["status"] == "terminated"
        for s in client.get("/api/sandboxes", headers=_auth(client)).json()
    )
    msgs = client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()["messages"]
    assert "<video" in msgs[-1]["content"]
    media = client.get(tut["output"]["video"]["url"], headers=_auth(client))
    assert media.status_code == 200

    # Editing one state re-voices only that line and publishes version 2.
    n = len(spoken)
    edit = {"narration": ["Now open the browser and visit localhost."]}
    res = client.patch(
        f"/api/tutorials/{tut['id']}/states/browser", json=edit, headers=_auth(client)
    )
    assert res.status_code == 202
    job2 = _wait(client, res.json()["job"]["id"], {"succeeded", "failed"})
    assert job2["status"] == "succeeded", job2["error"]
    assert spoken[n:] == ["Now open the browser and visit localhost."]
    tut2 = client.get(f"/api/tutorials/{tut['id']}", headers=_auth(client)).json()
    assert tut2["version"] == 2
    st = next(s for s in tut2["graph"]["states"] if s["id"] == "browser")
    assert st["narration"][0]["text"] == "Now open the browser and visit localhost."
    assert len(rendered) == 2


def test_already_installed_hides_install_state(client, monkeypatch):
    _fakes(monkeypatch)
    _, job = _make(client, pipeline.SKIP)
    assert job["status"] == "succeeded", job["error"]
    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    assert "install" not in [s["id"] for s in tut["graph"]["states"]]
    assert "Install nginx" not in tut["graph"]["steps"]


def test_tutorials_are_private(client, anon):
    assert anon.get("/api/tutorials").status_code == 401
    assert client.get("/api/tutorials/t_0000", headers=_auth(client)).status_code == 404


def test_edit_ops_become_state_edits():
    op = pipeline.EditOp(state="title", field="narration", value="Line one.\n\nLine two.")
    assert op.to_edit().narration == ["Line one.", "Line two."]
    assert pipeline.EditOp(state="verify", field="zoom", value="9").to_edit().zoom == 3.0
    assert pipeline.EditOp(state="write", field="label", value=" New ").to_edit().label == "New"
