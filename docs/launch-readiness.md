# Cueframe: what's left before real users

The state of `main` after PR #24, and what still has to be built, fixed or set up before the
public can sign up and use Cueframe. The longer-term roadmap is in [production-plan.md](production-plan.md).

Legend: **Blocker** = must be done before launch. **Fix** = known quality issue. **Verify** =
built but not yet proven end to end.

## 1. Blockers: infrastructure and accounts

| # | Item | State today | What to do | Needs from owner |
|---|---|---|---|---|
| 1 | Hosting, domain, HTTPS | No deploy config in the repo (no Dockerfile/Procfile) | Container image, HTTPS host, `CUEFRAME_PUBLIC_URL`, `CUEFRAME_COOKIE_SECURE=1` | Host choice, domain, DNS |
| 2 | Separate job worker | `JobManager` runs inside the web process (`app/main.py`); a deploy or crash marks running videos `interrupted` | Run jobs in their own worker process; web only enqueues and streams events | – |
| 3 | Database and backups | SQLite file, no backups | Managed Postgres (or SQLite + Litestream) with daily backups; the privacy page promises deleted data leaves backups within 30 days | DB provider |
| 4 | Payments | No Stripe code; plan buttons go to the dashboard/contact page | Stripe Checkout, customer portal, webhooks that set the plan (the plan switch already exists), receipts, video packs | Stripe account (test keys first) |
| 5 | Sign-in | Username/password only; `login_identity` hook ready for Google | Firebase Google sign-in route + button; email verification and password reset | Firebase project |
| 6 | Email | No email provider | "Video ready", 80% of limit, receipts, password reset | Resend/Postmark account |
| 7 | Legal | Terms, privacy, refunds, acceptable use are drafts | Company name and address, real `CUEFRAME_CONTACT_EMAIL`, lawyer review, refund rules matching Stripe | Company details, lawyer |
| 8 | Delete my data | Conversations can be deleted; deleting uploads, R2 videos and the whole account is not proven | Account deletion that removes uploads, videos (R2) and logs, as the privacy page says | – |
| 9 | Turn on the guards | Captcha, error tracking and alerts are built but off without keys | Set `CUEFRAME_TURNSTILE_SITE_KEY/SECRET`, `SENTRY_DSN`, `CUEFRAME_ALERT_WEBHOOK`; tune `CUEFRAME_DAILY_SPEND_CAP_USD` (default $25) | Cloudflare, Sentry, Slack/Discord |
| 10 | Secrets and admin | Keys come from the local `.env` | Store `MODEL_API_KEY`, ElevenLabs, R2 and Modal tokens as host secrets; strong `CUEFRAME_ADMIN_PASSWORD`; uptime monitor on `/api/health` | – |

## 2. Fixes: video quality and accuracy

Found in the 7 production reruns of the paper and model videos (PR #24).

| # | Issue | State |
|---|---|---|
| 11 | Large numbers misread by the voice ("3,947" heard as "3,147") | **Fixed, not rerun:** spoken in words, captioned as written |
| 12 | Numbers shown without their conditions ("0.25 s" without "one A100, BF16") | **Fixed, not rerun:** planner told to keep conditions next to the number |
| 13 | "Z-Image" spoken as "Seed Image" | **Open:** needs a pronunciation list for names (ElevenLabs pronunciation dictionary or respelling) |
| 14 | Possible extra syllable after "94.25 percent" | **Open:** listen again on the next rerun |
| 15 | Number speech ("point", "percent", numbers in words) is English only | **Open:** Hindi and other narration languages need their own number wording |
| 16 | Planning takes 1–2 min and up to 3 attempts; a video takes 1.7–4 min | **Improve:** fewer rejected plans (crowded boxes), stream progress |
| 17 | A photo with several questions teaches only the first | **Open:** ask which one, or teach each |

## 3. Verify: built but not proven end to end

- Final paper and model rerun after fixes 11–12.
- The narration-lengthening rewrite on a short video, with its word cap and kept conditions.
- "≥" spoken as "at least"; the short-arrow fallback for crowded diagrams; asking again after a malformed AI answer.
- Concept boards from an uploaded or linked paper; charts with negative numbers.
- Real phones and Safari; Hindi text on web pages.
- Alerts reaching Slack/Sentry; sign-up with the captcha on; sign-up and message rate limits.
- Usage: no charge for a failed video, two requests racing for the last free video, the monthly reset, and limits on each paid plan.
- Load: several users making videos at once (`CUEFRAME_MAX_CONCURRENT_JOBS` is 2 per process today).

## 4. Launch order

1. Hosting, worker, database with backups (1–3, 10).
2. Payments, Google sign-in, email (4–6).
3. Legal and data deletion (7–8), guards on (9).
4. Rerun and fix video quality (11–17); run the checks in section 3.
5. Soft launch to 20–50 invited creators and students, watch `/admin` Health and daily spend, then open sign-up.
