"""Board renderer entry point; runs the same on a server or in a sandbox:

    python -m app.board.engine check  JOB   # build every op, report LaTeX/geometry errors
    python -m app.board.engine render JOB   # JOB/board.mp4, rendered in parallel parts

JOB holds plan.json and timeline.json. Both commands print one JSON line."""

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCENE = Path(__file__).with_name("scene.py")
ROOT = Path(__file__).resolve().parents[2]


def _env(job: Path, tex: str = "tex", **extra: str) -> dict[str, str]:
    return {**os.environ, "BOARD_JOB": str(job), "BOARD_TEX": str(job / tex),
            "PYTHONPATH": str(ROOT), **extra}  # fmt: skip


def check(job: Path) -> dict:
    """Build every op once (this also compiles and caches all the LaTeX)."""
    os.environ.update(_env(job), BOARD_MEDIA=str(job / "media"))
    from app.board.contracts import BoardPlan
    from app.board.scene import Builder

    plan = BoardPlan.model_validate_json((job / "plan.json").read_text())
    b = Builder(plan)
    b.title()
    rows = b.all(strict=False)
    cams = [it.cam for row in rows for it in row]
    return {
        "errors": b.errors,
        "board_height": round(3.55 - b.board.bottom(), 2),
        "camera_moves": len({round(c, 2) for c in cams}),
        "lowest_camera": min(cams or [0]),
    }


def _part(job: Path, i: int, n: int) -> Path:
    media = job / f"media{i}"
    # Each part gets its own copy of the LaTeX cache: Manim writes temp files beside it.
    if (job / "tex").is_dir():
        shutil.copytree(job / "tex", job / f"tex{i}", dirs_exist_ok=True)
    cmd = [sys.executable, "-m", "manim", "render", str(SCENE), "BoardScene",
           "-r", "1920,1080", "--fps", "30", "--disable_caching", "--progress_bar", "none",
           "-v", "WARNING", "--media_dir", str(media), "-o", f"part{i}"]  # fmt: skip
    env = _env(job, f"tex{i}", BOARD_PART=f"{i}/{n}", BOARD_MEDIA=str(media))
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    out = next(media.rglob(f"part{i}.mp4"), None)
    if p.returncode or out is None:
        raise RuntimeError(f"part {i} failed: {(p.stderr or p.stdout)[-1500:]}")
    return out


def render(job: Path, parts: int = 0) -> dict:
    t0 = time.monotonic()
    tl = json.loads((job / "timeline.json").read_text())
    n = max(1, min(parts or os.cpu_count() or 1, len(tl["steps"]), 8))
    with ThreadPoolExecutor(n) as pool:
        outs = list(pool.map(lambda i: _part(job, i, n), range(n)))
    (job / "parts.txt").write_text("".join(f"file '{p}'\n" for p in outs))
    video = job / "board.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                    str(job / "parts.txt"), "-c", "copy", str(video)], check=True)  # fmt: skip
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(video)], capture_output=True, text=True, check=True).stdout)  # fmt: skip
    return {
        "video": str(video),
        "parts": n,
        "seconds": round(dur, 3),
        "expected": tl["seconds"],
        "render_seconds": round(time.monotonic() - t0, 1),
    }


def main() -> None:
    cmd, job = sys.argv[1], Path(sys.argv[2])
    try:
        parts = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        out = check(job) if cmd == "check" else render(job, parts)
    except Exception as exc:  # noqa: BLE001 - reported to the caller as JSON
        out = {"error": f"{type(exc).__name__}: {exc}"[-3000:]}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
