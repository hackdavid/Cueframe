import dataclasses
import subprocess

from fastapi.testclient import TestClient

from app import alerts, endcard, languages, usage
from app.main import create_app
from tests.test_usage import _login, _make


def _app(settings, **kw):
    return TestClient(create_app(dataclasses.replace(settings, **kw)))


def test_signup_referral_and_bonus(client):
    h, inviter = _login(client, "inviter")
    ref = client.get("/api/usage", headers=h).json()["referral"]
    assert ref["url"].endswith(f"/r/{ref['code']}") and ref["bonus_per_friend"] == 3
    r = client.get(f"/r/{ref['code']}", follow_redirects=False)
    assert r.status_code == 303 and "signup=1" in r.headers["location"]
    client.cookies.clear()
    body = {"username": "friend1", "password": "friend-pass-1", "ref": ref["code"]}
    res = client.post("/api/auth/signup", json=body)
    assert res.status_code == 201 and res.json()["user"]["plan"] == "free"
    assert client.post("/api/auth/signup", json=body).status_code == 409
    fh = {"Authorization": f"Bearer {res.json()['token']}"}
    assert _make(client, fh)[0]["result"] == {"title": "ok"}
    _make(client, fh)  # only the first video credits the inviter
    u = client.get("/api/usage", headers=h).json()
    assert u["bonus_videos"] == 3 and u["limits"]["total"] == 6 and u["limits"]["video"] == 4
    assert u["referral"]["invited"] == 1 and u["referral"]["credited"] == 1


def test_signup_closed_and_rate_limits(settings):
    with _app(settings, open_signup=False) as c:
        body = {"username": "nobody", "password": "long-pass-1"}
        assert c.post("/api/auth/signup", json=body).status_code == 403
    with _app(settings, rate_login_per_min=3) as c:
        codes = [c.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code
                 for _ in range(4)]  # fmt: skip
        assert codes == [401, 401, 401, 429]
    with _app(settings, rate_messages_per_min=2) as c:
        tok = c.post("/api/auth/login", json={"username": "admin", "password": "pw-test"})
        h = {"Authorization": f"Bearer {tok.json()['token']}"}
        codes = [c.post("/api/messages", headers=h, json={"content": "hi"}).status_code
                 for _ in range(3)]  # fmt: skip
        assert codes[-1] == 429 and "Retry-After" in c.post(
            "/api/messages", headers=h, json={"content": "hi"}).headers  # fmt: skip


def test_captcha_required_when_configured(settings):
    with _app(settings, turnstile_secret="s", turnstile_site_key="k") as c:
        assert "cf-turnstile" in c.get("/login?signup=1").text
        body = {"username": "botty", "password": "long-pass-1"}
        assert c.post("/api/auth/signup", json=body).status_code == 400


def test_daily_spend_cap_refuses_and_alerts(settings, caplog):
    alerts.reset()
    with _app(settings, daily_spend_cap_usd=0.35) as c:
        h, _ = _login(c, "spender")
        assert _make(c, h)[0]["result"] == {"title": "ok"}  # 0.15
        assert _make(c, h)[0]["result"] == {"title": "ok"}  # 0.30 -> 80% alert
        job, cid = _make(c, h)
        assert job["result"]["limit_reached"] == "daily_capacity"
        msgs = c.get(f"/api/conversations/{cid}", headers=h).json()["messages"]
        assert "today's video capacity" in msgs[-1]["content"]
        tok = c.post("/api/auth/login", json={"username": "admin", "password": "pw-test"})
        admin = {"Authorization": f"Bearer {tok.json()['token']}"}
        st = c.get("/api/admin/stats", headers=admin).json()
        assert st["spend_today_usd"] == 0.3 and st["spend_cap_usd"] == 0.35
        assert {a["fields"]["alert"][:8] for a in st["alerts"]} == {"spend80:", "spend100"}
        assert st["jobs"]["test.metered"]["statuses"]["succeeded"] == 3
        assert st["jobs"]["test.metered"]["success_rate"] == 1.0
        assert _make(c, admin)[0]["result"] == {"title": "ok"}  # admins skip the cap


def test_failed_job_alerts(client):
    alerts.reset()
    h, _ = _login(client, "failer")
    assert _make(client, h, fail=True)[0]["status"] == "failed"
    admin = {"Authorization": f"Bearer {client.token}"}
    logs = client.get("/api/admin/logs?logger=cueframe.alert", headers=admin).json()
    assert any("failed: RuntimeError: boom" in e["message"] for e in logs)


def test_share_page_public(client):
    store = client.app.state.store
    h, user = _login(client, "sharer")
    art = client.app.state.settings.artifacts_dir / "tutorials/t1"
    art.mkdir(parents=True)
    (art / "v.mp4").write_bytes(b"mp4")
    t = store.create_tutorial(user["id"], None, "j1", "board", "Solve 3x - 5 = 16",
                              {"problem": "Solve 3x - 5 = 16"})  # fmt: skip
    vid = {"backend": "local", "key": "tutorials/t1/v.mp4", "url": "/media/artifacts/x"}
    store.update_tutorial(t["id"], output={"video": vid, "thumbnail": vid,
                                           "stats": {"duration": 45, "checks": 4}})  # fmt: skip
    r = client.post(f"/api/tutorials/{t['id']}/share", headers=h, json={"public": True})
    slug = r.json()["slug"]
    client.cookies.clear()
    page = client.get(f"/v/{slug}")
    assert page.status_code == 200 and "og:video" in page.text and "Make your own" in page.text
    assert client.get(f"/v/{slug}/video.mp4").content == b"mp4"
    assert client.get(f"/v/{slug}/../../x").status_code == 404
    client.post(f"/api/tutorials/{t['id']}/share", headers=h, json={"public": False})
    assert client.get(f"/v/{slug}").status_code == 404
    assert client.get(f"/share/{t['id']}", follow_redirects=False).status_code == 303


def test_end_card_appends_two_seconds(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=1920x1080:rate=30:duration=1", "-f", "lavfi", "-i",
                    "sine=duration=1", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)  # fmt: skip
    out = endcard.append(src, "https://cueframe.ai/r/abcd1234")
    probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
    dur = subprocess.run([*probe, str(out)], capture_output=True, text=True, check=True)
    assert 2.9 < float(dur.stdout) < 3.2


def test_languages():
    assert languages.detect("Teach me 3x - 5 = 16 in Hindi") == "hi"
    assert languages.detect("hindi mein samjhao") == "hi"
    assert languages.detect("x² − 5x + 6 = 0 को हल करना सिखाइए") == "hi"
    assert languages.detect("explain integration in Spanish") == "es"
    assert languages.detect("make a reel about vllm") is None
    assert "Hindi (हिन्दी)" in languages.instruction("hi") and languages.instruction(None) == ""
    assert usage.costs('{"video": 1}')["video"] == 1


def test_end_card_title_clears_qr(tmp_path):
    from PIL import Image, ImageDraw

    png = endcard.card(tmp_path / "c.png", "https://cueframe.ai/r/abcd1234")
    d = ImageDraw.Draw(Image.open(png))
    title = "Made with Cueframe"
    size = next(s for s in range(92, 39, -4)
                if d.textlength(title, font=endcard._font(s)) <= endcard.QR_X - 390)  # fmt: skip
    assert 310 + d.textlength(title, font=endcard._font(size)) < endcard.QR_X


def test_board_image_has_script_fonts():
    from app.sandbox.modal_box import BOARD_APT

    assert "fonts-noto-core" in BOARD_APT
