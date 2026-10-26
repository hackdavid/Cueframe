"""Single-account login wall. The default account comes from
CUEFRAME_ADMIN_USERNAME / CUEFRAME_ADMIN_PASSWORD and is re-synced on start.
Tokens work as a browser cookie or as `Authorization: Bearer <token>` (Postman, MCP)."""

import hashlib
import hmac
import secrets
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import Store

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
    if not user or not verify_password(password, user["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    store.create_session(_token_hash(token), user["id"])
    return token, {"id": user["id"], "username": user["username"]}


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
