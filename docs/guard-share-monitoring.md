# Abuse guard, sharing, monitoring and languages

## Abuse and cost guard
- **Sign-up:** `POST /api/auth/signup` is on when `CUEFRAME_OPEN_SIGNUP=1` and creates
  Free accounts. A "Create a free account" switch is on `/login`; `/login?signup=1` opens it.
- **Captcha:** Cloudflare Turnstile on sign-up when `CUEFRAME_TURNSTILE_SITE_KEY` and
  `CUEFRAME_TURNSTILE_SECRET` are set; it's off when they're unset. Keys are free at
  dash.cloudflare.com → Turnstile.
- **Rate limits (429 + Retry-After):**
  - login: `CUEFRAME_RATE_LOGIN_PER_MIN` per IP (10);
  - sign-up: `CUEFRAME_RATE_SIGNUP_PER_HOUR` per IP (5);
  - chat messages: `CUEFRAME_RATE_MESSAGES_PER_MIN` per account (20).
  
  They're kept in memory per web process; move them to Redis when there are several.
- **Daily spend cap:** each counted video has an estimated cost (`app.usage.COST_USD`:
  lesson $0.15, video $0.40, edit $0.10; override with `CUEFRAME_VIDEO_COSTS`).
  - When today's total (UTC) plus the new video would pass `CUEFRAME_DAILY_SPEND_CAP_USD`
    ($25), the video is refused with a "today's capacity" reply. It isn't counted
    against the plan.
  - Alerts go out at 80% and 100%.
  - Admins are not capped.
  - The per-user sandbox limits (`CUEFRAME_USER_DAILY_SANDBOX_MINUTES`, …) still apply.

## Share pages, end card and referrals
- **Get a share link:** this link is under every finished video in chat (`/share/{id}`).
  It publishes the video at `/v/{slug}` and opens that page.
  - The page shows the player, the question, step/check counts, "Make your own, free",
    and X/WhatsApp/LinkedIn buttons. Its OpenGraph video tags make link previews play.
  - `POST /api/tutorials/{id}/share {"public": false}` unpublishes it.
  - Media is served by `/v/{slug}/video.mp4|poster.png|captions.srt`: a 1-hour R2 link,
    or the local file.
- **End card:** videos on plans in `CUEFRAME_WATERMARK_PLANS` (`free`) end with a 2 s "Made
  with Cueframe" card. Its QR code points to the owner's referral link.
- **Referrals:**
  - Every user has a link `/r/{code}` (dashboard sidebar → Copy). It sets a 30-day
    `cf_ref` cookie and opens sign-up.
  - When the invited friend's first video succeeds, the inviter gets
    `CUEFRAME_REFERRAL_BONUS` (3) bonus videos. They're added to their monthly total and
    video caps, once per friend.

## Monitoring
- **Sentry:** `SENTRY_DSN` turns on error tracking for unhandled request errors and failed
  jobs. Personal data isn't sent.
- **Alerts:** these are written to the central log as `cueframe.alert` and POSTed to
  `CUEFRAME_ALERT_WEBHOOK` (Slack or Discord incoming webhook):
  - each failed job;
  - spend at 80% and 100% of the cap.
  
  Repeats of the same alert are dropped for an hour (a day for spend).
- **`/admin` → Health and `GET /api/admin/stats?hours=24`:** for each job kind, its
  statuses, success rate, average and p95 seconds, and top errors. Also today's usage,
  estimated spend against the cap, running sandboxes and recent alerts.

## Languages
- **How to ask:** "Teach me … in Hindi", "hindi mein samjhao …", or a question written in
  Devanagari (also Bengali, Tamil, Telugu, Arabic, Cyrillic, Japanese, Chinese). The
  narration and captions then come in that language. 17 languages: `app/languages.py`.
- **What stays the same:**
  - Maths lessons write the board in maths notation and short English labels.
  - Tutorials keep commands, code and on-screen text as they are.
- **Voice:** the same ElevenLabs multilingual voice is used. `ELEVENLABS_VOICE_ID_HI`
  (etc.) picks a native voice per language.
- **Fonts:** tutorial captions are drawn by Remotion, so the render host needs fonts for the
  script, e.g. `apt-get install fonts-noto-core` for Devanagari.
