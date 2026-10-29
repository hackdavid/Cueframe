"""Plan limits and usage tracking for video generation.

Every metered job (a maths lesson, a tutorial/reel/explainer, an edit) reserves one usage
record before any planning or sandbox work starts. The reservation is checked against the
user's plan (`app.plans.LIMITS`) for the current calendar month (UTC) atomically, so two
chats can't both take the last free video. Failed or cancelled jobs are refunded.
"""

import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app import alerts
from app.db import COUNTED_USAGE, new_id, now
from app.log import fields, get_logger
from app.plans import LIMIT_KEYS, PLAN_NAMES, plan_limits

log = get_logger("usage")
CATEGORIES = ("lesson", "video", "edit")
LABELS = {
    "total": "videos",
    "lesson": "maths lessons",
    "video": "tutorials, reels or explainers",
    "edit": "video edits",
}


# Estimated provider cost of one video (Modal + ElevenLabs + model calls), for the global
# daily spend cap. Override with CUEFRAME_VIDEO_COSTS='{"video": 0.5}'.
COST_USD = {"lesson": 0.15, "video": 0.40, "edit": 0.10}


@lru_cache
def costs(overrides: str) -> dict[str, float]:
    return {**COST_USD, **(json.loads(overrides) if overrides.strip() else {})}


def today() -> str:
    d = datetime.now(UTC)
    return datetime(d.year, d.month, d.day, tzinfo=UTC).isoformat(timespec="milliseconds")


def spend(store: Any, settings: Any, since: str | None = None) -> float:
    """Estimated spend of counted videos since `since` (default: today, UTC)."""
    price = costs(settings.video_costs)
    totals = store.usage_totals({"since": since or today()})
    return round(sum(price.get(c, 0) * n for c, by in totals.items()
                     for s, n in by.items() if s in COUNTED_USAGE), 2)  # fmt: skip


def limits_for(settings: Any, user: dict[str, Any]) -> dict[str, int | None]:
    """The plan's limits plus referral bonus videos (added to the video caps)."""
    lim = plan_limits(settings.plan_limits, user.get("plan") or settings.default_plan)
    bonus = user.get("bonus_videos") or 0
    for k in ("total", "video"):
        if lim[k] is not None:
            lim[k] += bonus
    return lim


def video_kind(mode: str) -> str:
    return {"influencer": "reel", "explainer": "explainer"}.get(mode, "tutorial")


def period(at: datetime | None = None) -> tuple[str, str]:
    """Start of this calendar month and of the next one (UTC), as stored timestamps."""
    at = at or datetime.now(UTC)
    start = datetime(at.year, at.month, 1, tzinfo=UTC)
    end = datetime(at.year + at.month // 12, at.month % 12 + 1, 1, tzinfo=UTC)
    return start.isoformat(timespec="milliseconds"), end.isoformat(timespec="milliseconds")


def over_limit(limits: dict[str, int | None], counts: dict[str, int], category: str) -> str | None:
    """The limit that one more `category` video would break, or None."""
    cap = limits.get(category)
    if cap is not None and counts.get(category, 0) + 1 > cap:
        return category
    total = limits.get("total")
    made = counts.get("lesson", 0) + counts.get("video", 0)
    if category != "edit" and total is not None and made + 1 > total:
        return "total"
    return None


def summary(store: Any, settings: Any, user: dict[str, Any]) -> dict[str, Any]:
    plan = user.get("plan") or settings.default_plan
    limits = limits_for(settings, user)
    start, end = period()
    counts = store.usage_counts(user["id"], start)
    used = {c: counts.get(c, 0) for c in CATEGORIES}
    used["total"] = used["lesson"] + used["video"]
    left = {k: None if limits[k] is None else max(0, limits[k] - used[k]) for k in LIMIT_KEYS}
    for c in ("lesson", "video"):  # what's left of a kind is also capped by the total
        if left["total"] is not None:
            left[c] = left["total"] if left[c] is None else min(left[c], left["total"])
    return {"plan": plan, "plan_name": PLAN_NAMES.get(plan, plan.title()), "limits": limits,
            "used": used, "remaining": left, "period_start": start, "resets_at": end,
            "bonus_videos": user.get("bonus_videos") or 0}  # fmt: skip


def refusal(plan: str, limit: str, cap: int | None, resets_at: str) -> str:
    day = datetime.fromisoformat(resets_at).strftime("%-d %B")
    name = PLAN_NAMES.get(plan, plan.title())
    return (
        f"You've used all {cap} {LABELS[limit]} on the **{name}** plan this month. "
        f"It resets on {day}. [See plans](/pricing) to make more."
    )


def gate(ctx: Any, category: str, kind: str) -> dict[str, Any] | None:
    """Reserve one `category` video for the job's user. Returns None to go ahead, or (after
    explaining in chat) the job result to stop with when the plan's limit is reached."""
    user = ctx.store.get_user(ctx.user_id) if ctx.user_id else None
    if user is None:
        log.warning("unmetered job: no user", extra=fields(category=category, kind=kind))
        return None
    plan = user["plan"] or ctx.settings.default_plan
    limits = limits_for(ctx.settings, user)
    start, end = period()
    cap, cost = ctx.settings.daily_spend_cap_usd, costs(ctx.settings.video_costs)[category]
    capped = cap > 0 and user.get("role") != "admin"

    def blocked(counts: dict[str, int]) -> str | None:
        if over := over_limit(limits, counts, category):
            return over
        if capped and spend(ctx.store, ctx.settings) + cost > cap:
            return "daily_capacity"
        return None

    row = {"id": new_id("use"), "user_id": user["id"], "session_id": ctx.session_id,
           "conversation_id": ctx.conversation_id, "job_id": ctx.id, "category": category,
           "kind": kind, "plan": plan, "status": "reserved", "created_at": now()}  # fmt: skip
    counts, blocked_by = ctx.store.reserve_usage(row, start, blocked)
    if blocked_by is None:
        log.info("usage reserved", extra=fields(category=category, kind=kind, plan=plan))
        if capped and (spent := spend(ctx.store, ctx.settings)) >= 0.8 * cap:
            alerts.alert(ctx.settings, f"spend80:{today()[:10]}",
                         f"Daily spend at ${spent:.2f} of the ${cap:.2f} cap",
                         level=logging.WARNING, every=86400)  # fmt: skip
        return None
    blocked = blocked_by
    if blocked == "daily_capacity":
        alerts.alert(ctx.settings, f"spend100:{today()[:10]}",
                     f"Daily spend cap ${cap:.2f} reached; new videos refused until 00:00 UTC",
                     every=86400)  # fmt: skip
        if ctx.conversation_id:
            ctx.reply("Cueframe has reached today's video capacity. Please try again after "
                      "midnight UTC; nothing was counted against your plan.")  # fmt: skip
        ctx.emit("usage.limit", {"plan": plan, "limit": blocked, "category": category})
        return {"limit_reached": blocked, "plan": plan}
    log.warning("usage limit reached", extra=fields(category=category, kind=kind, plan=plan,
                                                    limit=blocked, used=counts))  # fmt: skip
    if ctx.conversation_id:
        ctx.reply(refusal(plan, blocked, limits[blocked], end))
    ctx.emit("usage.limit", {"plan": plan, "limit": blocked, "category": category})
    return {"limit_reached": blocked, "plan": plan}
