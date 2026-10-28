from dataclasses import replace
from datetime import UTC, datetime

from app import autopilot
from tests.test_planner import GOOD, StubLLM, _plan, _setup, _step
from tests.test_tutorial import _auth, _wait

BRIEF = (
    "Trending item (GitHub Trending): JetBrains/thinkrail [repo]\n"
    "Why it's trending: #1 on GitHub Trending, 1,075 stars today (2026-10-26)\n"
    "Page to open: https://github.com/JetBrains/thinkrail\nRelated pages: none\n"
)


def test_social_caption_and_hashtags_come_from_the_plan_and_brief():
    spec = {"title": "This IDE Got 1,075 Stars", "summary": "A tour of thinkrail.",
            "tags": ["open source", "AI agents", "github", "IDE"], "brief": BRIEF}  # fmt: skip
    s = autopilot.social(spec, "repo")
    assert s["item"] == "JetBrains/thinkrail"
    assert "1,075 stars today (2026-10-26)" in s["caption"]
    assert s["caption"].endswith("Source: https://github.com/JetBrains/thinkrail")
    assert s["hashtags"] == ["#GitHub", "#OpenSource", "#AIAgents", "#IDE"]


def test_at_time_parses_hh_mm_only():
    assert autopilot.at_time("07:30").hour == 7
    assert autopilot.at_time("") is None and autopilot.at_time("25:00") is None


def test_tick_starts_each_topic_once_a_day_after_the_set_time(client):
    s = client.app.state
    s.settings = replace(s.settings, autopilot_at="07:00")
    assert client.portal.call(autopilot.tick, s, datetime(2026, 10, 27, 6, 59, tzinfo=UTC)) == []
    rows = client.portal.call(autopilot.tick, s, datetime(2026, 10, 27, 7, 1, tzinfo=UTC))
    assert sorted(r["topic"] for r in rows) == ["paper", "repo"]
    assert client.portal.call(autopilot.tick, s, datetime(2026, 10, 27, 9, 0, tzinfo=UTC)) == []
    for r in rows:
        job = _wait(client, r["job_id"], {"succeeded", "failed"})
        assert job["input"]["job_input"] == {"auto": True}
    client.portal.call(autopilot.tick, s, datetime(2026, 10, 27, 9, 1, tzinfo=UTC))
    runs = client.get("/api/autopilot", headers=_auth(client)).json()["runs"]
    assert {r["status"] for r in runs} == {"failed"}  # no model here, so no video


def test_skips_items_already_covered(client):
    s = client.app.state
    uid = s.store.get_user_by_name("admin")["id"]
    row = s.store.add_autopilot(uid, "2026-10-26", "repo", "x")
    s.store.update_autopilot(row["id"], item="JetBrains/thinkrail", status="ready")
    assert "JetBrains/thinkrail" in autopilot.request_for(s.store, uid, "repo")
    assert "JetBrains" not in autopilot.request_for(s.store, uid, "paper")


def test_unattended_run_answers_questions_and_publishes_with_caption(client, monkeypatch):
    clean = [GOOD[0], _step("run", command="ls"), *GOOD[2:]]
    _setup(client, monkeypatch, StubLLM([_plan(clean)]))
    res = client.post("/api/autopilot/run", json={"topics": ["repo"]}, headers=_auth(client))
    assert res.status_code == 202
    (run,) = res.json()["runs"]
    job = _wait(client, run["job_id"], {"succeeded", "failed", "waiting_input"}, timeout=20)
    assert job["status"] == "succeeded", job
    (row,) = client.get("/api/autopilot", headers=_auth(client)).json()["runs"]
    assert row["status"] == "ready" and row["video_url"] and row["caption"]
    assert "#GitHub" in row["hashtags"]
    again = client.post("/api/autopilot/run", json={}, headers=_auth(client)).json()
    assert [r["topic"] for r in again["runs"]] == ["repo", "paper"]  # the repo one finished
    r = client.patch(f"/api/autopilot/{row['id']}", json={"review": "approved"},
                     headers=_auth(client))  # fmt: skip
    assert r.json()["review"] == "approved"
    assert client.patch(f"/api/autopilot/{row['id']}", json={"review": "maybe"},
                        headers=_auth(client)).status_code == 422  # fmt: skip


def test_autopilot_page_needs_login(anon):
    assert anon.get("/autopilot", follow_redirects=False).status_code == 303
