"""Central Meta Model API client (Responses API). Every model call in the app goes
through `MetaClient` so retries, structured output, usage/cost accounting and
logging live in one place."""

import asyncio
import json
import re
import time
from typing import Any, TypeVar

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
        res = await self.respond(schema=schema, **kw)
        try:
            return schema.model_validate(json.loads(_json_text(res.text))), res
        except (json.JSONDecodeError, ValidationError) as exc:
            log.error(
                "llm output violated contract",
                extra=fields(
                    purpose=kw.get("purpose"), schema=schema.__name__, text=res.text[:300]
                ),
            )
            raise LLMError(f"{schema.__name__} contract violated: {exc}") from exc
