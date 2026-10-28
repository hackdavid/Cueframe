from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE, logout, optional_user
from app.samples import BY_SLUG, SAMPLES

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "landing.html", {"user": optional_user(request), "samples": SAMPLES[:3]}
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
    current = BY_SLUG[slug] if slug else SAMPLES[0]
    return templates.TemplateResponse(
        request,
        "samples.html",
        {"user": optional_user(request), "samples": SAMPLES, "current": current},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard") -> Response:
    if not next.startswith("/") or next.startswith("//"):
        next = "/dashboard"
    if optional_user(request):
        return RedirectResponse(next, 303)
    return templates.TemplateResponse(request, "login.html", {"user": None, "next": next})


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


@router.get("/autopilot", response_class=HTMLResponse)
def autopilot_page(request: Request) -> Response:
    user = optional_user(request)
    if user is None:
        return RedirectResponse("/login?next=/autopilot", 303)
    return templates.TemplateResponse(request, "autopilot.html", {"user": user})
