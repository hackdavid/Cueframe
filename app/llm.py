"""Central Meta Model API client (Responses API). Every model call in the app goes
through `MetaClient` so retries, structured output, usage/cost accounting and
logging live in one place."""

import asyncio
import json
import re
import time
from typing import Any, TypeVar, get_args

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.log import fields, get_logger

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens: (input, cached input, output). Source: dev.meta.ai/docs/pricing-rate-limits
PRICES: dict[str, tuple[float, float, float]] = {
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}
WEB_SEARCH_USD = 2.50 / 1000


class LLMError(RuntimeError):
    pass


class Usage(BaseModel):
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            **{k: getattr(self, k) + getattr(other, k) for k in Usage.model_fields},
        )


class LLMResponse(BaseModel):
    id: str
    model: str
    text: str = Field(description="Text of the final assistant message")
    output: list[dict[str, Any]] = []
    usage: Usage
    latency_s: float

    def search_calls(self) -> list[dict[str, Any]]:
        return [o for o in self.output if o.get("type") == "web_search_call"]


def _usage(model: str, raw: dict[str, Any], searches: int) -> Usage:
    inp = raw.get("input_tokens", 0)
    cached = (raw.get("input_tokens_details") or {}).get("cached_tokens", 0)
    out = raw.get("output_tokens", 0)
    p_in, p_cached, p_out = PRICES.get(model, PRICES["muse-spark-1.3"])
    cost = ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1e6
    return Usage(
        input_tokens=inp,
        cached_tokens=cached,
        output_tokens=out,
        reasoning_tokens=(raw.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
        web_searches=searches,
        cost_usd=round(cost + searches * WEB_SEARCH_USD, 6),
    )


_TAIL = re.compile(r"[^a-z0-9_]")


def _models(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for a in get_args(annotation) for m in _models(a)]


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def repair_keys(schema: type[BaseModel], data: Any) -> int:
    """Restore keys the model garbled by appending junk, e.g. "x_range phenomena",
    "axesNormals" or "answer前方", so their values aren't silently dropped. Returns the
    number of keys restored."""
    if isinstance(data, list):
        return sum(repair_keys(schema, d) for d in data)
    if not isinstance(data, dict):
        return 0
    names = sorted(schema.model_fields, key=len, reverse=True)
    fixed = 0
    for key in [k for k in data if k not in schema.model_fields]:
        for name in names:
            if key.startswith(name) and _TAIL.match(key[len(name)]) and name not in data:
                data[name] = data.pop(key)
                fixed += 1
                break
    for name, field in schema.model_fields.items():
        v = data.get(name)
        if v is None and name in data and not field.is_required() and field.default is not None:
            del data[name]  # "trending": null where the field is a plain bool with a default
            fixed += 1
            continue
        if field.annotation is str and _number(v):  # "id": 0 where a handle is a string
            data[name], fixed = str(v), fixed + 1
        elif field.annotation == list[str] and isinstance(v, list) and any(map(_number, v)):
            data[name], fixed = [str(x) if _number(x) else x for x in v], fixed + 1
        for model in _models(field.annotation):
            fixed += repair_keys(model, data.get(name))
    return fixed


def _json_text(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


class MetaClient:
    def __init__(
        self,
        api_key: str | None,
        base_url: str = "https://api.meta.ai/v1",
        timeout: float = 180,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.configured = bool(api_key)
        self.max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key or ''}"},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kw: Any) -> "MetaClient":
        return cls(settings.meta_api_key, settings.meta_base_url, **kw)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def respond(
        self,
        *,
        purpose: str,
        model: str,
        input: str | list[dict[str, Any]],
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        max_tool_calls: int | None = None,
        include: list[str] | None = None,
        schema: type[BaseModel] | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        if not self.configured:
            raise LLMError("MODEL_API_KEY is not configured")
        body: dict[str, Any] = {"model": model, "input": input}
        optional = {
            "instructions": instructions,
            "tools": tools,
            "max_tool_calls": max_tool_calls,
            "include": include,
            "reasoning": {"effort": reasoning_effort} if reasoning_effort else None,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                }
            }
            if schema
            else None,
        }
        body.update({k: v for k, v in optional.items() if v is not None})
        started = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                res = await self._http.post(
                    "/responses", json=body, timeout=timeout or httpx.USE_CLIENT_DEFAULT
                )
            except httpx.HTTPError as exc:
                err: str = f"{type(exc).__name__}: {exc}"
            else:
                if res.status_code == 200:
                    break
                err = f"HTTP {res.status_code}: {res.text[:300]}"
                if res.status_code not in (408, 409, 429) and res.status_code < 500:
                    log.error("llm call rejected", extra=fields(purpose=purpose, error=err))
                    raise LLMError(err)
            if attempt == self.max_retries:
                log.error("llm call failed", extra=fields(purpose=purpose, error=err))
                raise LLMError(err)
            log.warning("llm retry", extra=fields(purpose=purpose, attempt=attempt + 1, error=err))
            await asyncio.sleep(1.5 * 2**attempt)
        data = res.json()
        if data.get("error"):
            raise LLMError(str(data["error"]))
        output = data.get("output", [])
        messages = [o for o in output if o.get("type") == "message"]
        text = (
            "".join(c.get("text", "") for c in messages[-1].get("content", [])) if messages else ""
        )
        searches = sum(
            1
            for o in output
            if o.get("type") == "web_search_call" and o.get("action", {}).get("type") == "search"
        )
        out = LLMResponse(
            id=data.get("id", ""),
            model=model,
            text=text,
            output=output,
            usage=_usage(model, data.get("usage", {}), searches),
            latency_s=round(time.monotonic() - started, 2),
        )
        log.info(
            "llm call",
            extra=fields(
                purpose=purpose,
                model=model,
                input_tokens=out.usage.input_tokens,
                output_tokens=out.usage.output_tokens,
                searches=searches,
                cost_usd=out.usage.cost_usd,
                latency_s=out.latency_s,
            ),
        )
        return out

    async def parse(self, schema: type[T], **kw: Any) -> tuple[T, LLMResponse]:
        """`respond` with a JSON-schema output contract, validated by Pydantic."""
        for attempt in (1, 2):  # a malformed answer is usually a one-off: ask once more
            res = await self.respond(schema=schema, **kw)
            try:
                data = json.loads(_json_text(res.text))
                if fixed := repair_keys(schema, data):
                    log.warning(
                        "llm output had garbled keys",
                        extra=fields(purpose=kw.get("purpose"), schema=schema.__name__, keys=fixed),
                    )
                return schema.model_validate(data), res
            except (json.JSONDecodeError, ValidationError) as exc:
                log.error(
                    "llm output violated contract",
                    extra=fields(
                        purpose=kw.get("purpose"), schema=schema.__name__, attempt=attempt,
                        text=res.text[:300],
                    ),
                )  # fmt: skip
                err = exc
        raise LLMError(f"{schema.__name__} contract violated: {err}") from err
