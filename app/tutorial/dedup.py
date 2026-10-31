"""Catch narration lines that say the same thing twice. Every place narration is
written or rewritten (plan, retell, repair, review, shown-check, edit) calls this;
duplicates come back as problems-style strings so the existing repair loops reword one
of the pair. Short functional lines ('Now run it.') recur legitimately and are exempt.

Blocking checks (a plan that can't ship, an edit that's refused) are gated behind
CUEFRAME_DEDUP_ENFORCE=1 until the threshold is tuned on real plans; the non-blocking
guards (reverting a rewrite that introduces a repeat) are always on."""

import os
import re

STOP = frozenset(
    "the a an and to of it this that we you your i me let lets now so here there "
    "is are was were be been in on at for with as by from or but not no its it's".split()
)
FLOOR = 4  # fewer content tokens than this can't be a meaningful repeat
THRESHOLD = 0.75  # below this, steps sharing one fact phrase ('ours 48.7') stay legit


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower())} - STOP


def enforce() -> bool:
    return os.environ.get("CUEFRAME_DEDUP_ENFORCE", "").lower() in ("1", "true", "yes")


def pair_dup(a: str, b: str, threshold: float = THRESHOLD) -> bool:
    """True when two lines say the same thing: identical after normalizing, or high
    content-word overlap."""
    if not a.strip() or not b.strip():
        return False
    if _norm(a) == _norm(b):
        return True
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < FLOOR or len(tb) < FLOOR:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def repeats(text: str, lines: dict[str, str], threshold: float = THRESHOLD) -> str | None:
    """The key of a line `text` repeats, else None."""
    for k, v in lines.items():
        if pair_dup(text, v, threshold):
            return k
    return None


def duplicates(lines: dict[str, str], threshold: float = THRESHOLD) -> list[str]:
    """Problems-style strings for every pair of lines that say the same thing."""
    out, items = [], [(k, v) for k, v in lines.items() if v.strip()]
    for i, (ka, va) in enumerate(items):
        for kb, vb in items[i + 1 :]:
            if pair_dup(va, vb, threshold):
                out.append(
                    f"lines {ka!r} and {kb!r} say the same thing ({va[:60]!r}); reword "
                    "one so each sentence is heard once"
                )
    return out


def spoken_lines(plan) -> dict[str, str]:
    """A tutorial plan's spoken lines keyed the way `to_spec` keys them."""
    lines = {"intro": plan.intro, "outro": plan.outro}
    for s in plan.steps:
        lines[s.id] = s.narration
        if s.after.strip():
            lines[f"{s.id}_done"] = s.after
    return lines


def say_lines(plan) -> dict[str, str]:
    """A board plan's spoken lines, keyed by step number."""
    return {f"step{i}": st.say for i, st in enumerate(plan.steps, 1)}
