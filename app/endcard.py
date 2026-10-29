"""The 2 s "Made with Cueframe" end card (with a referral QR code) added to videos made on
plans listed in CUEFRAME_WATERMARK_PLANS (Free by default)."""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from app.log import fields, get_logger

log = get_logger("endcard")
SECONDS = 2.0
W, H = 1920, 1080
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]  # fmt: skip


def _font(size: int, bold: bool = True) -> Any:
    from PIL import ImageFont

    for path in FONTS if bold else FONTS[::-1]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def card(path: Path, url: str) -> Path:
    import qrcode
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), "#09090b")
    d = ImageDraw.Draw(img)
    for y in range(H):  # soft violet-to-sky glow along the top
        a = max(0.0, 1 - y / 520)
        d.line([(0, y), (W, y)], fill=(int(9 + 40 * a), int(9 + 18 * a), int(11 + 70 * a)))
    d.rounded_rectangle((150, 330, 270, 450), 28, fill="#8b5cf6")
    d.text((210, 390), "C", font=_font(78), fill="white", anchor="mm")
    d.text((310, 340), "Made with Cueframe", font=_font(92), fill="white")
    d.text((314, 470), "One sentence or one photo in,", font=_font(48, False), fill="#d4d4d8")
    d.text((314, 535), "a checked video lesson out.", font=_font(48, False), fill="#d4d4d8")
    d.text((314, 650), url.split("://")[-1], font=_font(46), fill="#38bdf8")
    qr = qrcode.QRCode(border=2, box_size=12)
    qr.add_data(url)
    q = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
    q = q.resize((420, 420))
    img.paste(q, (1350, 330))
    d.text((1560, 790), "Scan to make your own", font=_font(34, False), fill="#a1a1aa",
           anchor="mm")  # fmt: skip
    img.save(path)
    return path


def append(video: Path, url: str, out: Path | None = None) -> Path:
    """`video` followed by the end card (re-encoded once; audio padded with silence)."""
    out = out or video.with_name(video.stem + "-card.mp4")
    png = card(video.with_name("endcard.png"), url)
    graph = (
        f"[0:v]scale={W}:{H},fps=30,format=yuv420p,setsar=1[mv];"
        "[0:a]aresample=44100,aformat=channel_layouts=stereo[ma];"
        f"[1:v]scale={W}:{H},fps=30,format=yuv420p,setsar=1[cv];"
        "[mv][ma][cv][2:a]concat=n=2:v=1:a=1[v][a]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-loop", "1", "-framerate",
         "30", "-t", f"{SECONDS}", "-i", str(png), "-f", "lavfi", "-t", f"{SECONDS}", "-i",
         "anullsrc=r=44100:cl=stereo", "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(out)],
        check=True,
    )  # fmt: skip
    return out


async def brand(ctx: Any, video: Path) -> Path:
    """Add the end card when the job's user is on a watermarked plan."""
    user = ctx.store.get_user(ctx.user_id) if ctx.user_id else None
    plan = (user or {}).get("plan") or ctx.settings.default_plan
    if user is None or plan not in ctx.settings.watermark_plans:
        return video
    code = ctx.store.ensure_ref_code(user["id"])
    url = f"{ctx.settings.public_url.rstrip('/')}/r/{code}"
    ctx.phase("brand", "Adding the Cueframe end card")
    out = await asyncio.to_thread(append, video, url)
    log.info("end card added", extra=fields(plan=plan))
    return out
