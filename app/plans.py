"""Plans and packages shown on /pricing (prices in USD; checkout is wired up at launch),
and the monthly video limits each plan enforces."""

import json
from functools import lru_cache

PLANS = [
    {
        "slug": "free",
        "name": "Free",
        "price": "$0",
        "per": "/month",
        "blurb": "Try it with your own questions.",
        "cta": "Start free",
        "features": [
            "3 videos a month (photo or text maths lessons)",
            "1 of them can be a tutorial, reel or explainer",
            "1080p 16:9 with voice and captions",
            "'Made with Cueframe' end card",
        ],
    },
    {
        "slug": "student",
        "name": "Student Pro",
        "price": "$9",
        "per": "/month",
        "blurb": "Unlimited teacher-style maths lessons.",
        "cta": "Get Student Pro",
        "popular": True,
        "features": [
            "Unlimited maths lessons (fair use, 150/month)",
            "Photo of a question → full lesson",
            "Child, school and undergrad levels",
            "'Your turn' practice in every lesson",
            "No watermark, download MP4 + captions",
        ],
    },
    {
        "slug": "creator",
        "name": "Creator",
        "price": "$29",
        "per": "/month",
        "blurb": "Tutorials, reels and explainers for your channel.",
        "cta": "Get Creator",
        "features": [
            "30 videos a month, any format",
            "Real sandbox runs, fast-forward and cuts",
            "Links, PDFs, slides and repos as sources",
            "'Trending for you' feed: one click to a video",
            "Captions and hashtags for every post",
            "Edit from chat, commercial use",
        ],
    },
    {
        "slug": "studio",
        "name": "Studio",
        "price": "$99",
        "per": "/month",
        "blurb": "For teams and dev-relations.",
        "cta": "Contact us",
        "features": [
            "120 videos a month, 3 seats",
            "Your brand colours, intro and outro",
            "Priority rendering",
            "Shared video library",
        ],
    },
]

PACKAGES = [
    {
        "name": "Classroom",
        "price": "$199/month",
        "text": "Up to 200 students, a lesson library in syllabus order, class share links "
        "and a teacher dashboard.",
    },
    {
        "name": "Video packs",
        "price": "from $15",
        "text": "10 extra videos that never expire, on any paid plan.",
    },
    {
        "name": "API",
        "price": "from $0.50/video",
        "text": "Generate checked lessons and tutorials from your own app, with a webhook "
        "when each video is ready.",
    },
]

FAQ = [
    (
        "Is the maths really checked?",
        "Yes. Every line of algebra and calculus, and every "
        "geometry claim, is verified with SymPy before it's drawn. A second teacher pass reviews "
        "the method. If a check fails, the plan is fixed or the lesson is refused.",
    ),
    (
        "Are tutorials real or simulated?",
        "Real. Software tutorials are rehearsed and recorded "
        "in a cloud sandbox, and the results you see came from that run.",
    ),
    (
        "Can I cancel any time?",
        "Yes. Plans renew monthly or yearly and can be cancelled from "
        "your account; you keep access until the end of the period.",
    ),
    (
        "Do you keep my photos and files?",
        "Only as long as needed to make and show your video. "
        "You can delete them, and your videos, at any time. See the privacy policy.",
    ),
    ("Is there a yearly discount?", "Yes, yearly plans are 2 months free."),
]

# Videos each plan can make per calendar month (UTC). "total" counts maths lessons and
# tutorials/reels/explainers together; "lesson", "video" and "edit" cap each kind on its own.
# A missing key means no limit. Override with CUEFRAME_PLAN_LIMITS, e.g.
# '{"free": {"total": 5}, "team": {"total": 300}}'.
LIMIT_KEYS = ("total", "lesson", "video", "edit")
LIMITS: dict[str, dict[str, int]] = {
    "free": {"total": 3, "video": 1, "edit": 3},
    "student": {"lesson": 150, "video": 1, "edit": 20},
    "creator": {"total": 30, "edit": 100},
    "studio": {"total": 120, "edit": 400},
    "unlimited": {},
}
PLAN_NAMES = {p["slug"]: p["name"] for p in PLANS} | {"unlimited": "Unlimited"}


@lru_cache
def _table(overrides: str) -> dict[str, dict[str, int]]:
    extra = json.loads(overrides) if overrides.strip() else {}
    if not isinstance(extra, dict) or not all(isinstance(v, dict) for v in extra.values()):
        raise ValueError("CUEFRAME_PLAN_LIMITS must be a JSON object of plan -> limits")
    return {**LIMITS, **{p: {**LIMITS.get(p, {}), **v} for p, v in extra.items()}}


def plans_known(overrides: str = "") -> list[str]:
    return list(_table(overrides))


def plan_limits(overrides: str, plan: str) -> dict[str, int | None]:
    """Limits for `plan`; an unknown plan gets the Free limits."""
    table = _table(overrides)
    lim = table.get(plan, table["free"])
    return {k: lim.get(k) for k in LIMIT_KEYS}
