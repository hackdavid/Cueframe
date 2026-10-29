"""Login wall. The admin account comes from CUEFRAME_ADMIN_USERNAME /
CUEFRAME_ADMIN_PASSWORD and is re-synced on start; admins add password accounts in /admin,
and external sign-ins (Firebase Google later) go through `login_identity`.
Every login gets a public session id that usage records and logs are tagged with.
Tokens work as a browser cookie or as `Authorization: Bearer <token>` (Postman, MCP)."""

import hashlib
import hmac
import secrets
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import Store
from app.log import fields, get_logger

log = get_logger("auth")

COOKIE = "cf_session"
bearer = HTTPBearer(auto_error=False, description="Token from POST /api/auth/login")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$")[2]
    return hmac.compare_digest(candidate, digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(store: Store, username: str, password: str) -> tuple[str, dict[str, Any]] | None:
    user = store.get_user_by_name(username)
    if (
        not user
        or user["auth_provider"] != "password"
        or not verify_password(password, user["password_hash"])
    ):
        log.warning("login failed", extra=fields(username=username))
        return None
    return _start_session(store, user)


def login_identity(
    store: Store, provider: str, uid: str, *, email: str | None, name: str | None, plan: str
) -> tuple[str, dict[str, Any]]:
    """Sign in a user verified by an external provider. A Firebase route would verify the
    ID token (firebase_admin.auth.verify_id_token) and call this with `provider="google"`."""
    return _start_session(store, store.upsert_identity(provider, uid, email=email, name=name,
                                                       plan=plan))  # fmt: skip


def _start_session(store: Store, user: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    token = secrets.token_urlsafe(32)
    sid = store.create_session(_token_hash(token), user["id"])
    log.info("login", extra=fields(user_id=user["id"], session_id=sid,
                                   provider=user["auth_provider"]))  # fmt: skip
    keep = ("id", "username", "email", "display_name", "plan", "role")
    return token, {**{k: user.get(k) for k in keep}, "session_id": sid}


def logout(store: Store, token: str) -> None:
    store.delete_session(_token_hash(token))


def user_for_token(store: Store, token: str | None) -> dict[str, Any] | None:
    return store.user_for_session(_token_hash(token)) if token else None


def request_token(
    request: Request, credentials: HTTPAuthorizationCredentials | None = None
) -> str | None:
    if credentials:
        return credentials.credentials
    return request.cookies.get(COOKIE)


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    user = user_for_token(request.app.state.store, request_token(request, credentials))
    if user is None:
        raise HTTPException(401, "Not logged in", headers={"WWW-Authenticate": "Bearer"})
    return user


def optional_user(request: Request) -> dict[str, Any] | None:
    return user_for_token(request.app.state.store, request.cookies.get(COOKIE))


def websocket_user(ws: WebSocket) -> dict[str, Any] | None:
    token = ws.query_params.get("token") or ws.cookies.get(COOKIE)
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    return user_for_token(ws.app.state.store, token)


User = Annotated[dict[str, Any], Depends(current_user)]


def admin_user(user: User) -> dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admins only")
    return user


Admin = Annotated[dict[str, Any], Depends(admin_user)]
