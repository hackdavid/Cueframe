import asyncio

from app import usage
from app.auth import hash_password
from app.jobs import HANDLERS, job_handler


@job_handler("test.metered")
async def _metered(ctx):
    ji = ctx.input["job_input"]
    if stop := usage.gate(ctx, ji["category"], ji["category"]):
        return stop
    if ji.get("fail"):
        raise RuntimeError("boom")
    return {"title": "ok"}


def _login(c, name, plan="free"):
    store = c.app.state.store
    store.upsert_user(name, hash_password("pw-12345678"), plan=plan)
    tok = c.post("/api/auth/login", json={"username": name, "password": "pw-12345678"}).json()
    return {"Authorization": f"Bearer {tok['token']}"}, tok["user"]


def _make(c, h, category="lesson", fail=False):
    res = c.post("/api/messages", headers=h, json={
        "content": "x", "job_kind": "test.metered",
        "job_input": {"category": category, "fail": fail}}).json()  # fmt: skip
    jid = res["job"]["id"]
    for _ in range(200):
        job = c.get(f"/api/jobs/{jid}", headers=h).json()
        if job["status"] not in ("queued", "running"):
            return job, res["conversation"]["id"]
        asyncio.run(asyncio.sleep(0.01))
    raise AssertionError("job did not finish")


def test_over_limit_rules():
    free = {"total": 3, "lesson": None, "video": 1, "edit": 3}
    assert usage.over_limit(free, {"lesson": 2}, "lesson") is None
    assert usage.over_limit(free, {"lesson": 3}, "lesson") == "total"
    assert usage.over_limit(free, {"video": 1}, "video") == "video"
    assert usage.over_limit(free, {"lesson": 3}, "edit") is None
    assert usage.over_limit({"total": None}, {"lesson": 999}, "lesson") is None
    assert usage.period()[0].endswith("-01T00:00:00.000+00:00")


def test_free_plan_stops_after_three_and_refunds_failures(client):
    h, user = _login(client, "freebie")
    assert user["plan"] == "free" and user["session_id"].startswith("ses_")
    job, _ = _make(client, h, fail=True)
    assert job["status"] == "failed"
    for _ in range(3):
        assert _make(client, h)[0]["result"] == {"title": "ok"}
    job, cid = _make(client, h)
    assert job["result"] == {"limit_reached": "total", "plan": "free"}
    msgs = client.get(f"/api/conversations/{cid}", headers=h).json()["messages"]
    assert "You've used all 3 videos on the **Free** plan" in msgs[-1]["content"]
    u = client.get("/api/usage", headers=h).json()
    assert u["used"]["total"] == 3 and u["remaining"]["total"] == 0
    assert u["remaining"]["video"] == 0 and len(u["recent"]) == 4
    assert {r["status"] for r in u["recent"]} == {"succeeded", "failed"}
    assert all(r["session_id"] == user["session_id"] for r in u["recent"])


def test_video_sub_limit_and_admin_plan_change(client):
    h, user = _login(client, "creator1")
    assert _make(client, h, "video")[0]["result"] == {"title": "ok"}
    assert _make(client, h, "video")[0]["result"]["limit_reached"] == "video"
    admin = {"Authorization": f"Bearer {client.token}"}
    r = client.patch(f"/api/admin/users/{user['id']}", headers=admin, json={"plan": "creator"})
    assert r.json()["plan"] == "creator"
    assert client.patch(f"/api/admin/users/{user['id']}", headers=admin,
                        json={"plan": "gold"}).status_code == 422  # fmt: skip
    assert _make(client, h, "video")[0]["result"] == {"title": "ok"}


def test_admin_only_filters_by_user_and_session(client):
    h, user = _login(client, "viewer")
    _make(client, h)
    assert client.get("/api/admin/usage", headers=h).status_code == 403
    admin = {"Authorization": f"Bearer {client.token}"}
    recs = client.get(f"/api/admin/usage?user_id={user['id']}", headers=admin).json()
    assert [r["username"] for r in recs["records"]] == ["viewer"]
    assert recs["totals"] == {"lesson": {"succeeded": 1}}
    logs = client.get(f"/api/admin/logs?session_id={user['session_id']}", headers=admin).json()
    msgs = {entry["message"] for entry in logs}
    assert {"login", "usage reserved", "job finished"} <= msgs
    assert all(entry["user_id"] == user["id"] for entry in logs)
    warn = client.get("/api/admin/logs?level=warning&q=freebie", headers=admin).json()
    assert all(e["levelno"] >= 30 for e in warn)
    users = client.get("/api/admin/users", headers=admin).json()
    assert any(u["username"] == "viewer" and u["usage"]["used"]["total"] == 1 for u in users)
    assert client.get("/api/usage", headers=admin).json()["plan"] == "unlimited"


def test_admin_creates_users_and_identity_login(client):
    admin = {"Authorization": f"Bearer {client.token}"}
    body = {"username": "teacher", "password": "long-enough", "plan": "studio"}
    assert client.post("/api/admin/users", headers=admin, json=body).json()["plan"] == "studio"
    assert client.post("/api/admin/users", headers=admin, json=body).status_code == 409
    from app.auth import login_identity

    store = client.app.state.store
    t1, u1 = login_identity(store, "google", "g-1", email="a@b.c", name="A", plan="free")
    t2, u2 = login_identity(store, "google", "g-1", email="a@b.c", name="A B", plan="free")
    assert u1["id"] == u2["id"] and u1["session_id"] != u2["session_id"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t2}"}).status_code == 200
    bad = client.post("/api/auth/login", json={"username": "a@b.c", "password": ""})
    assert bad.status_code in (401, 422)


def test_handlers_registered():
    assert {"tutorial.make", "board.make", "tutorial.edit"} <= set(HANDLERS)
