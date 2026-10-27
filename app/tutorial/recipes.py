"""Tutorial recipes: a verified task (`spec.json`: steps, highlights, camera hints)
plus its narration (`narration.json`). Each lives in `tutorials/<id>/`."""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2] / "tutorials"


@cache
def recipes() -> dict[str, dict[str, Any]]:
    out = {}
    for spec in sorted(ROOT.glob("*/spec.json")):
        r = json.loads(spec.read_text())
        r["lines"] = json.loads((spec.parent / "narration.json").read_text())
        out[r["id"]] = r
    return out


def get(recipe_id: str) -> dict[str, Any]:
    r = recipes().get(recipe_id)
    if r is None:
        raise KeyError(f"Unknown tutorial recipe: {recipe_id}")
    return json.loads(json.dumps(r))  # callers edit their own copy


def match(text: str) -> str | None:
    """Keyword match, used when no LLM is configured."""
    low = text.lower()
    for rid, r in recipes().items():
        if any(re.search(rf"\b{re.escape(k)}\b", low) for k in r.get("match", [])):
            return rid
    return None


def catalog() -> str:
    return "\n".join(f"- `{rid}`: {r['title']} — {r['summary']}" for rid, r in recipes().items())
