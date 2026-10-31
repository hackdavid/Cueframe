"""Scope guard (off_scope routing + canned refusal), the conversation context brief,
and per-conversation voice selection."""

import time

from app.db import Store
from app.handlers import tutorial as th
from app.tutorial.voice import conversation_voice


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


def test_off_scope_message_gets_canned_refusal(client, monkeypatch):
    async def route(ctx, has_tutorial, brief=""):
        return th.Route(intent="off_scope")

    monkeypatch.setattr(th, "route", route)
    res = client.post("/api/messages", json={"content": "write me a python script"},
                      headers=_auth(client)).json()  # fmt: skip
    job = _wait(client, res["job"]["id"], {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert job["result"]["off_scope"] is True
    msgs = client.get(f"/api/conversations/{res['conversation']['id']}",
                      headers=_auth(client)).json()["messages"]  # fmt: skip
    assert "I only make videos" in msgs[-1]["content"]
    assert client.get("/api/tutorials", headers=_auth(client)).json() == []


def test_scope_prompt_refuses_general_assistant_work():
    flat = " ".join(th.ROUTER.split())  # the prompt is line-wrapped
    for phrase in ("debugging code", "small talk", "not a general assistant"):
        assert phrase in flat
    assert "off_scope" in th.Route.model_fields["intent"].annotation.__args__


def test_context_brief_summarizes_messages_and_videos(tmp_path):
    store = Store(tmp_path / "t.sqlite3")
    user = store.upsert_user("sam", "hash")
    conv = store.create_conversation(user["id"], "chat")
    store.add_message(conv["id"], "user", "Make a tutorial about nginx")
    store.add_message(conv["id"], "assistant", "Done!  " * 200)  # trimmed to 200 chars
    tut = store.create_tutorial(user["id"], conv["id"], "j_1", "auto", "Nginx tutorial", {})
    store.update_tutorial(tut["id"], status="failed", version=1)
    tut = store.get_tutorial(tut["id"])  # refetch: the create row is stale

    class Ctx:
        pass

    ctx = Ctx()
    ctx.store = store
    brief = th.context_brief(ctx, conv, [tut])
    assert "user: Make a tutorial about nginx" in brief
    assert 'cueframe: ' in brief and len(brief) < 1000
    assert '"Nginx tutorial" (v1): FAILED' in brief


def test_conversation_voice_resolution(tmp_path):
    store = Store(tmp_path / "t.sqlite3")
    user = store.upsert_user("sam", "hash")
    conv = store.create_conversation(user["id"], "chat")

    class Ctx:
        pass

    ctx = Ctx()
    ctx.store = store
    ctx.conversation_id = conv["id"]

    assert conversation_voice(ctx) == ""  # no pick yet
    store.update_conversation(conv["id"], voice_id="voice-123")
    assert conversation_voice(ctx) == "voice-123"
    store.update_conversation(conv["id"], voice_id=None)  # cleared
    assert conversation_voice(ctx) == ""


def test_voice_picker_endpoints(client):
    res = client.post("/api/conversations", json={"title": "voice test"}, headers=_auth(client))
    cid = res.json()["id"]
    res = client.patch(f"/api/conversations/{cid}", json={"voice_id": "abc"},
                       headers=_auth(client))  # fmt: skip
    assert res.status_code == 200 and res.json()["voice_id"] == "abc"
    conv = client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()
    assert conv["voice_id"] == "abc"
    # clearing the pick
    res = client.patch(f"/api/conversations/{cid}", json={"voice_id": ""}, headers=_auth(client))
    assert res.json()["voice_id"] is None
    # title-only patches still work and don't touch the voice
    client.patch(f"/api/conversations/{cid}", json={"voice_id": "v2"}, headers=_auth(client))
    res = client.patch(f"/api/conversations/{cid}", json={"title": "renamed"},
                       headers=_auth(client))  # fmt: skip
    assert res.json()["title"] == "renamed" and res.json()["voice_id"] == "v2"
    # an empty patch is a 422
    assert client.patch(f"/api/conversations/{cid}", json={},
                        headers=_auth(client)).status_code == 422  # fmt: skip


def test_voices_endpoint_surfaces_library(client, monkeypatch):
    from app import voices

    async def fake_list():
        return [{"id": "v1", "name": "Alice", "gender": "female", "accent": "british",
                 "language": "en", "preview_url": "https://x/p.mp3", "category": "premade",
                 "age": "", "use_case": "", "description": ""}]  # fmt: skip

    monkeypatch.setattr(voices, "list_voices", fake_list)
    res = client.get("/api/voices", headers=_auth(client))
    assert res.status_code == 200 and res.json()[0]["name"] == "Alice"
    assert res.json()[0]["gender"] == "female"


def test_shared_voice_normalization():
    from app import voices

    raw = {"voice_id": "sv1", "public_user_id": "u9", "name": "Mishi", "gender": "female",
           "accent": "standard", "language": "hi", "cloned_by_count": 281,
           "preview_url": "https://x/sv1.mp3"}  # fmt: skip
    v = voices._normalize_shared(raw)
    assert v["category"] == "shared" and v["language"] == "hi"
    assert v["public_user_id"] == "u9" and v["uses"] == 281


def test_voices_normalize_labels():
    from app import voices

    raw = {"voice_id": "v9", "name": "Raj", "category": "premade",
           "labels": {"gender": "male", "accent": "indian", "age": "middle_aged"},
           "preview_url": "https://x/v9.mp3"}  # fmt: skip
    v = voices._normalize(raw)
    assert v["id"] == "v9" and v["accent"] == "indian" and v["language"] == "en"


def test_tutorial_graph_event_is_emitted(client, monkeypatch):
    """The storyboard's data: publish() announces the states before rendering."""
    from app.tutorial import pipeline
    from tests.test_tutorial import _fakes, _make

    _fakes(monkeypatch)
    cid, job = _make(client, pipeline.SHOW)
    assert job["status"] == "succeeded", job["error"]
    events = client.get(f"/api/events?after=0&conversation_id={cid}", headers=_auth(client)).json()
    graph = [e for e in events if e["type"] == "tutorial.graph"]
    assert graph, "no tutorial.graph event"
    states = graph[0]["payload"]["states"]
    assert {"id", "label"} <= set(states[0])
    assert len(states) >= 3
