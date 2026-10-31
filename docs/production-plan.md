# Cueframe: path to production

Positioning: **the AI video teacher that checks its own work.** One sentence or one photo in,
a narrated 16:9 lesson out: maths on a teacher's board (SymPy-checked), real software
tutorials recorded in a cloud sandbox, reels and explainers.

Priority order. Each item is one focused PR unless noted.

## P0: needed before the first paying user
| # | Item | What | Needs from owner |
|---|---|---|---|
| 1 | Hosting + domain | One VPS or Fly.io/Render app behind HTTPS, `CUEFRAME_*` env, R2 + Modal + ElevenLabs keys as secrets | Host choice, domain, DNS access |
| 2 | Worker process | Run `JobManager` in a separate worker so web restarts never kill a render; jobs resume from SQLite | – |
| 3 | Postgres | Move from SQLite to managed Postgres (jobs, events, users, usage) | DB provider |
| 4 | Accounts + email sign-up | Email/password + Google sign-in, email verification, password reset | Email sender (Resend/Postmark) |
| 5 | Plans + usage limits | Free / Student Pro / Creator / Studio quotas per month, enforced before a sandbox starts; per-account daily Modal-minute cap | – |
| 6 | Payments | Stripe Checkout + customer portal + webhooks → plan on the account; annual billing | Stripe account |
| 7 | Abuse + cost guard | Captcha on sign-up, rate limits, global daily Modal spend cap with alert | – |
| 8 | Legal review | Terms, privacy, refunds, acceptable use pages exist; have them reviewed for your country; set the real contact email (`CUEFRAME_CONTACT_EMAIL`) | Company name, address, lawyer review |
| 9 | Monitoring | Health check, error tracking (Sentry), uptime alert, job-failure alert, daily cost report | Sentry account |

## P1: growth (first 4–6 weeks)
| # | Item | What |
|---|---|---|
| 10 | Share pages | Public page per video (opt-in) with player, question and "Make your own" button; SEO-indexed |
| 11 | Watermark end card | 2 s "Made with Cueframe" card + QR on Free videos |
| 12 | Referral + creator program | Referral codes, 30% revenue share, creator dashboard |
| 13 | Vertical export | Optional 9:16 crop of the 16:9 video for Shorts/Reels/TikTok sharing |
| 14 | Languages | Hindi first, then Spanish/Arabic narration and captions |
| 15 | Lesson library | Classroom plan: save, order and share lessons with a class link |

## P2: product depth
| # | Item | What |
|---|---|---|
| 16 | Notebook tutorials | JupyterLab in the sandbox, CPU first, GPU fine-tunes as a paid add-on (designed: `docs/motion-scenes.md` § Notebook format) |
| 17 | Faster planning | Cache common question types, smaller model for routing, stream the plan |
| 18 | Edit lessons from chat | "slower", "explain step 3 again", "for a child", re-voice only changed steps |
| 19 | Public API + keys | Per-video billing, webhooks when a video is ready |
| 20 | Complex desktop apps | Photopea and similar canvas apps |

## Launch checklist
- [ ] P0 items 1–9 done and tested with a real paid checkout
- [ ] Samples page and homepage reviewed for claims
- [ ] 20–50 creators invited with free Pro
- [ ] Daily "question of the day" posting from the samples
