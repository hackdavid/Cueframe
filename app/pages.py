from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE, logout, optional_user

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> Response:
    return templates.TemplateResponse(request, "landing.html", {"user": optional_user(request)})


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
