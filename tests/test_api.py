import asyncio
import time

from app.jobs import JobContext, job_handler
from app.uploads import safe_filename


@job_handler("test.slow")
async def _slow(ctx: JobContext):
    ctx.phase("execute", "Sleeping")
    await asyncio.sleep(30)


@job_handler("test.boom")
async def _boom(ctx: JobContext):
    raise ValueError("kaboom")


def wait_for_job(client, job_id, timeout=10, headers=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still running")


def test_pages_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/dashboard").status_code == 200
    assert client.get("/dashboard/c/nope").status_code == 404
    assert client.get("/static/js/app.js").status_code == 200


def test_login_wall(anon):
    res = anon.get("/dashboard", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login?next=/dashboard"
    assert anon.get("/").status_code == 200
    assert anon.get("/api/conversations").status_code == 401
    assert anon.get("/media/uploads/x/y.png").status_code == 401
    bad = anon.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert bad.status_code == 401


def test_bearer_token_api_without_cookies(client, settings):
    """Postman/MCP style: no cookie, only Authorization header."""
    token = client.token
    client.cookies.clear()
    assert client.get("/api/conversations").status_code == 401
    h = {"Authorization": f"Bearer {token}"}
    up = client.post(
        "/api/uploads", headers=h, files=[("files", ("a.png", b"img", "image/png"))]
    ).json()[0]
    res = client.post(
        "/api/messages", headers=h, json={"content": "hi", "attachment_ids": [up["id"]]}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["message"]["source"] == "api"
    state = client.get(f"/api/conversations/{body['conversation']['id']}", headers=h).json()
    assert state["has_pending_job"] in (True, False)
    assert wait_for_job(client, body["job"]["id"], headers=h)["status"] == "succeeded"
    assert client.get(up["url"], headers=h).content == b"img"
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/conversations", headers=h).status_code == 401


def test_websocket_tracks_connection_and_pending_jobs(client):
    with client.websocket_connect("/api/ws?client=ui") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        conn_id = hello["connection_id"]
        ws.send_json({"type": "message.send", "content": "wait", "job_kind": "test.slow",
                      "request_id": "r1"})  # fmt: skip
        ack = next(f for f in iter(ws.receive_json, None) if f.get("request_id") == "r1")
        cid, job = ack["conversation"]["id"], ack["job"]
        assert job["connection_id"] == conn_id and ack["message"]["source"] == "ws"

        ws.send_json({"type": "open", "conversation_id": cid, "request_id": "r2"})
        state = next(f for f in iter(ws.receive_json, None) if f.get("request_id") == "r2")
        assert state["conversation"]["has_pending_job"] is True
        assert [j["id"] for j in state["conversation"]["active_jobs"]] == [job["id"]]
        assert [c["id"] for c in state["conversation"]["connections"]] == [conn_id]

        ws.send_json({"type": "job.cancel", "job_id": job["id"], "request_id": "r3"})
        frames = []
        while not any(
            f.get("type") == "event" and f["event"]["type"] == "job.cancelled" for f in frames
        ):
            frames.append(ws.receive_json())
        assert any(f.get("request_id") == "r3" for f in frames)
    conn = client.app.state.store.get_connection(conn_id)
    assert conn["conversation_id"] == cid and conn["disconnected_at"]
    assert client.get(f"/api/conversations/{cid}").json()["has_pending_job"] is False


def test_websocket_rejects_anonymous(anon):
    from starlette.websockets import WebSocketDisconnect

    try:
        with anon.websocket_connect("/api/ws") as ws:
            ws.receive_json()
        raise AssertionError("expected close")
    except WebSocketDisconnect as exc:
        assert exc.code == 4401


def test_upload_and_message_runs_background_job(client):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    res = client.post(
        "/api/uploads",
        files=[
            ("files", ("my photo?.png", png, "image/png")),
            ("files", ("notes.txt", b"hi", "text/plain")),
        ],
    )
    assert res.status_code == 201
    image, doc = res.json()
    assert image["kind"] == "image" and image["filename"] == "my_photo_.png"
    assert doc["kind"] == "file"
    assert client.get(image["url"]).content == png

    conv = client.post("/api/conversations", json={}).json()
    res = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Make a vertical video", "attachment_ids": [image["id"], doc["id"]]},
    )
    assert res.status_code == 201
    job = wait_for_job(client, res.json()["job"]["id"])
    assert job["status"] == "succeeded" and job["progress"] == 1.0

    data = client.get(f"/api/conversations/{conv['id']}").json()
    assert data["title"] == "Make a vertical video"
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant"]
    assert f"![my_photo_.png]({image['url']})" in data["messages"][1]["content"]

    types = [e["type"] for e in client.get(f"/api/events?conversation_id={conv['id']}").json()]
    for expected in (
        "job.queued",
        "job.started",
        "phase.changed",
        "job.progress",
        "message.created",
        "job.succeeded",
    ):
        assert expected in types
    assert types.index("job.started") < types.index("job.succeeded")


def test_cancel_and_failure(client):
    conv = client.post("/api/conversations", json={}).json()
    url = f"/api/conversations/{conv['id']}/messages"
    slow = client.post(url, json={"content": "wait", "job_kind": "test.slow"}).json()["job"]
    time.sleep(0.1)
    assert client.post(f"/api/jobs/{slow['id']}/cancel").json() == {"cancelled": True}
    assert wait_for_job(client, slow["id"])["status"] == "cancelled"

    boom = client.post(url, json={"content": "x", "job_kind": "test.boom"}).json()["job"]
    failed = wait_for_job(client, boom["id"])
    assert failed["status"] == "failed" and "kaboom" in failed["error"]

    assert client.post(url, json={"content": "x", "job_kind": "nope"}).status_code == 422
    assert client.post(url, json={"content": "  "}).status_code == 422


def test_restart_marks_running_jobs_interrupted(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "pw-test"})
        conv = c.post("/api/conversations", json={}).json()
        job = c.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "w", "job_kind": "test.slow"},
        ).json()["job"]
        c.app.state.store.update_job(job["id"], status="running")
        c.app.state.jobs._tasks.clear()  # simulate a crash: task is gone, row still says running
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "pw-test"})
        assert c.get(f"/api/jobs/{job['id']}").json()["status"] == "interrupted"


def test_upload_limit(client):
    res = client.post(
        "/api/uploads",
        files=[("files", ("big.bin", b"0" * (2 * 1024 * 1024), "application/octet-stream"))],
    )
    assert res.status_code == 413


def test_video_range_requests(client):
    up = client.post(
        "/api/uploads", files=[("files", ("clip.mp4", b"x" * 5000, "video/mp4"))]
    ).json()[0]
    assert up["kind"] == "video"
    res = client.get(up["url"], headers={"Range": "bytes=0-99"})
    assert res.status_code == 206 and len(res.content) == 100


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("") == "file"


def test_sample_videos_page_is_public(anon):
    page = anon.get("/samples")
    assert page.status_code == 200 and "/samples/media/python-docs-asyncio.mp4" in page.text
    assert anon.get("/samples/excalidraw-rectangle").status_code == 200
    assert "/samples/media/trending-repo-thinkrail.mp4" in page.text
    assert anon.get("/samples/text-classifier-sklearn").status_code == 200
    assert "/samples/media/reel-google-trends.mp4" in page.text
    assert anon.get("/samples/explainer-chronoreplay").status_code == 200
    assert "Learn maths like a teacher" in page.text
    assert "/samples/media/math-pythagoras-proof.mp4" in page.text
    assert anon.get("/samples/math-fractions-child").status_code == 200
    assert anon.get("/samples/nope").status_code == 404
    assert "/samples/math-photo-rectangle" in anon.get("/").text
    assert 'src="/samples/media/math-photo-rectangle.mp4"' in page.text  # shown first


def test_sample_media_redirects_to_signed_r2_link(anon, monkeypatch):
    storage = anon.app.state.storage
    assert anon.get("/samples/media/mdn-fetch-api.mp4").status_code == 404  # no R2 here
    monkeypatch.setattr(type(storage), "backend", property(lambda self: "r2"))
    monkeypatch.setattr(storage, "presign", lambda key, seconds: f"https://r2.test/{key}")
    res = anon.get("/samples/media/mdn-fetch-api.mp4", follow_redirects=False)
    assert res.status_code == 307 and res.headers["location"].endswith("samples/mdn-fetch-api.mp4")
    assert anon.get("/samples/media/other.mp4", follow_redirects=False).status_code == 404
    assert anon.get("/samples/media/mdn-fetch-api.txt", follow_redirects=False).status_code == 404
