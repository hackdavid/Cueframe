from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import legal
from app.auth import COOKIE, logout, optional_user
from app.config import get_settings
from app.plans import FAQ, PACKAGES, PLANS
from app.samples import BY_SLUG, MATH_SAMPLES, SAMPLES

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> Response:
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "user": optional_user(request),
            "samples": [*MATH_SAMPLES[:3], *SAMPLES[:3]],
            "plans": PLANS,
        },
    )


@router.get("/samples/media/{name}")
def sample_media(request: Request, name: str) -> Response:
    """Public sample videos and posters: a short-lived signed R2 link (Range requests work)."""
    slug, _, ext = name.rpartition(".")
    storage = request.app.state.storage
    if slug not in BY_SLUG or ext not in ("mp4", "jpg") or storage.backend != "r2":
        raise HTTPException(404, "Not found")
    url = storage.presign(storage.ref(f"samples/{slug}.{ext}")["key"], seconds=6 * 3600)
    return RedirectResponse(url, 307, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/samples", response_class=HTMLResponse)
@router.get("/samples/{slug}", response_class=HTMLResponse)
def samples_page(request: Request, slug: str | None = None) -> Response:
    if slug is not None and slug not in BY_SLUG:
        raise HTTPException(404, "Sample not found")
    current = BY_SLUG[slug] if slug else MATH_SAMPLES[0]
    return templates.TemplateResponse(
        request,
        "samples.html",
        {
            "user": optional_user(request),
            "samples": SAMPLES,
            "math": MATH_SAMPLES,
            "current": current,
        },  # fmt: skip
    )


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request) -> Response:
    ctx = {"user": optional_user(request), "plans": PLANS, "packages": PACKAGES, "faq": FAQ}
    return templates.TemplateResponse(request, "pricing.html", ctx)


def legal_page(request: Request) -> Response:
    slug = request.url.path.strip("/")
    email = get_settings().contact_email
    ctx = {"user": optional_user(request), "doc": legal.docs(email)[slug], "slug": slug,
           "email": email, "updated": legal.UPDATED}  # fmt: skip
    return templates.TemplateResponse(request, "legal.html", ctx)


for _slug in legal.docs(""):
    router.add_api_route(f"/{_slug}", legal_page, response_class=HTMLResponse)


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request, next: str = "/dashboard", signup: bool = False, ref: str = ""
) -> Response:
    if not next.startswith("/") or next.startswith("//"):
        next = "/dashboard"
    if optional_user(request):
        return RedirectResponse(next, 303)
    st = request.app.state.settings
    ctx = {"user": None, "next": next, "signup": signup and st.open_signup,
           "open_signup": st.open_signup, "ref": ref or request.cookies.get("cf_ref", ""),
           "turnstile_site_key": st.turnstile_site_key}  # fmt: skip
    return templates.TemplateResponse(request, "login.html", ctx)


@router.get("/r/{code}")
def referral_link(request: Request, code: str) -> Response:
    """Referral link (also in the QR on Free videos): remember the code, open sign-up."""
    response = RedirectResponse(f"/login?signup=1&ref={code[:40]}", 303)
    if request.app.state.store.user_by_ref(code[:40]):
        response.set_cookie("cf_ref", code[:40], max_age=60 * 60 * 24 * 30, samesite="lax")
    return response


@router.get("/share/{tid}")
def share_link(request: Request, tid: str) -> Response:
    """The chat's "Get a share link": publish the owner's video and open its public page."""
    user = optional_user(request)
    if not user:
        return RedirectResponse(f"/login?next=/share/{tid}", 303)
    store = request.app.state.store
    tut = store.get_tutorial(tid, user["id"])
    if not tut or not (tut.get("output") or {}).get("video"):
        raise HTTPException(404, "Video not found")
    return RedirectResponse(f"/v/{store.share_tutorial(tid)}", 303)


def _shared(request: Request, slug: str) -> dict:
    tut = request.app.state.store.tutorial_by_slug(slug)
    if not tut or not (tut.get("output") or {}).get("video"):
        raise HTTPException(404, "This video isn't shared")
    return tut


@router.get("/v/{slug}", response_class=HTMLResponse)
def shared_video(request: Request, slug: str) -> Response:
    """Public page for a shared video: player, what it teaches and "Make your own"."""
    tut = _shared(request, slug)
    store, st = request.app.state.store, request.app.state.settings
    spec = tut.get("spec") or {}
    board = spec.get("board") or {}
    about = spec.get("problem") or board.get("question") or spec.get("goal") or ""
    code = store.ensure_ref_code(tut["user_id"])
    stats = (tut.get("output") or {}).get("stats") or {}
    ctx = {"user": optional_user(request), "t": tut, "slug": slug, "about": about,
           "kind": "Maths lesson" if tut["recipe"] == "board" else "Video tutorial",
           "seconds": stats.get("duration"), "steps": stats.get("steps"),
           "checks": stats.get("checks"), "ref": code,
           "page_url": f"{st.public_url.rstrip('/')}/v/{slug}",
           "base": st.public_url.rstrip("/")}  # fmt: skip
    return templates.TemplateResponse(request, "share.html", ctx)


@router.get("/v/{slug}/{name}")
def shared_media(request: Request, slug: str, name: str) -> Response:
    """The shared video, poster or captions: a short-lived R2 link, or the local file."""
    out = _shared(request, slug)["output"]
    item = {"video.mp4": "video", "poster.png": "thumbnail", "captions.srt": "captions"}.get(name)
    stored = out.get(item) if item else None
    if not stored:
        raise HTTPException(404, "Not found")
    storage = request.app.state.jobs.storage
    if stored.get("backend") == "r2":
        return RedirectResponse(storage.presign(stored["key"], 3600), 302)
    path = (request.app.state.settings.artifacts_dir / stored["key"]).resolve()
    if (
        not path.is_relative_to(request.app.state.settings.artifacts_dir.resolve())
        or not path.exists()
    ):
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@router.get("/logout")
def logout_page(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        logout(request.app.state.store, token)
    response = RedirectResponse("/", 303)
    response.delete_cookie(COOKIE)
    return response


def _dashboard(request: Request, cid: str | None) -> Response:
    user = optional_user(request)
    if user is None:
        return RedirectResponse(f"/login?next={request.url.path}", 303)
    if cid and request.app.state.store.get_conversation(cid, user["id"]) is None:
        raise HTTPException(404, "Conversation not found")
    return templates.TemplateResponse(
        request, "dashboard.html", {"user": user, "conversation_id": cid}
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    return _dashboard(request, None)


@router.get("/dashboard/c/{cid}", response_class=HTMLResponse)
def dashboard_conversation(request: Request, cid: str) -> Response:
    return _dashboard(request, cid)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> Response:
    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", 303)
    if user.get("role") != "admin":
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request, "admin.html", {"user": user})
